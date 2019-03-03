from tqdm import tqdm
import random 
import os 
import numpy as np
import argparse
import subprocess
import shlex
import sys

import torch

from util import *
from eval import *

RANDOM_SEED = 12345
random.seed(RANDOM_SEED)
np.random.seed(RANDOM_SEED)
torch.manual_seed(RANDOM_SEED)
if torch.cuda.is_available():
    torch.cuda.manual_seed_all(RANDOM_SEED)

def train(args):
    if args.load_trained:
        epoch, arch, model, tokenizer, scores = load_checkpoint(args.pytorch_dump_path) 
    else:
        model, tokenizer = load_pretrained_model_tokenizer(args.model_type, device=args.device, chinese=args.chinese, num_labels=args.num_labels)
    train_dataset = DataGenerator(args.data_path, args.data_name, args.batch_size, tokenizer, "train", args.device, args.data_format)
    validate_dataset = DataGenerator(args.data_path, args.data_name, args.batch_size, tokenizer, "dev", args.device, args.data_format, label_map=train_dataset.label_map)
    test_dataset = DataGenerator(args.data_path, args.data_name, args.batch_size, tokenizer, "test", args.device, args.data_format, label_map=train_dataset.label_map)
    optimizer = init_optimizer(model, args.learning_rate, args.warmup_proportion, args.num_train_epochs, train_dataset.data_size, args.batch_size)
    
    model.train()
    global_step = 0
    best_score = 0
    step = 0
    for epoch in range(1, args.num_train_epochs+1):
        print("epoch {} ............".format(epoch))
        tr_loss = 0
        # random.shuffle(train_dataset)
        while True:
            batch = train_dataset.load_batch()
            if batch is None:
                break
            tokens_tensor, segments_tensor, mask_tensor, label_tensor = batch[:4]
            if args.model_type == "BertForNextSentencePrediction" or args.model_type == "BertForQuestionAnswering" \
                    or args.model_type == "BertForTokenClassification" or args.model_type == "BertForSequenceClassification":
                #print(tokens_tensor.shape, segments_tensor.shape, mask_tensor.shape, label_tensor.shape)
                loss = model(tokens_tensor, segments_tensor, mask_tensor, label_tensor)
            else:
                loss, logits = model(tokens_tensor, segments_tensor, mask_tensor, label_tensor)
            loss.backward()
            tr_loss += loss.item()
            optimizer.step()
            model.zero_grad()
            global_step += 1
            
            if args.eval_steps > 0 and step % args.eval_steps == 0:
                print("step: {}".format(step))
                best_score = eval_select(model, tokenizer, validate_dataset, test_dataset, args.pytorch_dump_path, best_score, epoch, args.model_type)
            
            step += 1

        print("[train] loss: {}".format(tr_loss))
        best_score = eval_select(model, tokenizer, validate_dataset, test_dataset, args.pytorch_dump_path, best_score, epoch, args.model_type)

    scores = test(args, split="test")
    print_scores(scores)

def eval_select(model, tokenizer, validate_dataset, test_dataset, model_path, best_score, epoch, arch):
    scores_dev = test(args, split="dev", model=model, tokenizer=tokenizer, test_dataset=validate_dataset)
    print_scores(scores_dev, mode="dev")
    scores_test = test(args, split="test", model=model, tokenizer=tokenizer, test_dataset=test_dataset)
    print_scores(scores_test)
     
    if scores_dev[1][0] > best_score:
        best_score = scores_dev[1][0]
        # Save pytorch-model
        model_path = "{}_{}".format(model_path, epoch)
        print("Save PyTorch model to {}".format(model_path))
        save_checkpoint(epoch, arch, model, tokenizer, scores_dev, model_path, test_dataset.label_map)

    return best_score

def print_scores(scores, mode="test"):
    print("")
    print("[{}] ".format(mode), end="")
    for sn, score in zip(scores[0], scores[1]):
        print("{}: {}".format(sn, score), end=" ")
    print("")

def save_checkpoint(epoch, arch, model, tokenizer, scores, filename, label_map):
    state = {
        'epoch': epoch,
        'arch': arch,
        'model': model,
        'tokenizer': tokenizer, 
        'scores': scores,
        'label_map': label_map
    }
    torch.save(state, filename)

def load_checkpoint(filename):
    print("Load PyTorch model from {}".format(filename))
    state = torch.load(filename)
    return state['epoch'], state['arch'], state['model'], state['tokenizer'], state['scores'], state['label_map']

def test(args, split="test", model=None, tokenizer=None, test_dataset=None):
    if model is None:
        epoch, arch, model, tokenizer, scores, label_map = load_checkpoint(args.pytorch_dump_path)
        assert test_dataset is None 
        print("Load {} set".format(split))
        test_dataset = DataGenerator(args.data_path, args.data_name, args.batch_size, tokenizer, split, args.device, args.data_format, label_map=label_map)
    
    model.eval()
    prediction_score_list, prediction_index_list, labels = [], [], []
    f = open(args.output_path, "w")
    f2 = open(args.output_path2, "w")
    qrelf = open(split + '.' + args.qrels_path, "w")

    lineno = 1
    label_map_reverse = {}
    for k in test_dataset.label_map:
        label_map_reverse[test_dataset.label_map[k]] = k
    qid_tensor, docid_tensor = None, None
    while True:
        batch = test_dataset.load_batch()
        if batch is None:
            break
        if len(batch) == 6:
            tokens_tensor, segments_tensor, mask_tensor, label_tensor, qid_tensor, docid_tensor = batch
        elif len(batch) == 5:
            tokens_tensor, segments_tensor, mask_tensor, label_tensor, qid_tensor = batch
        else:
            tokens_tensor, segments_tensor, mask_tensor, label_tensor = batch
        # print(tokens_tensor.shape, segments_tensor.shape, mask_tensor.shape)
        predictions = model(tokens_tensor, segments_tensor, mask_tensor)
        scores = predictions.cpu().detach().numpy()
        predicted_index = list(torch.argmax(predictions, dim=-1).cpu().numpy())
        predicted_score = list(predictions[:, 1].cpu().detach().numpy())
        prediction_score_list.extend(predicted_score)
        label_batch = list(label_tensor.cpu().detach().numpy())
        label_new = []
        predicted_index_new = []
        if args.data_format == "trec":
            qids = qid_tensor.cpu().detach().numpy()
            if docid_tensor is not None:
                docids = docid_tensor.cpu().detach().numpy()
            else:
                docids = list(range(lineno, lineno + len(labels)))
            for p, qid, docid, s, label in zip(predicted_index, qids, docids, \
            	scores, labels):
                f.write("{}\t{}\n".format(lineno, p))
                f2.write("{} Q0 {} {} {} bert\n".format(qid, docid, lineno, s[1]))
                qrelf.write("{} 0 {} {}\n".format(qid, docid, label))
                lineno += 1
        elif args.data_format == "ontonote":
            tokens =  tokens_tensor.cpu().detach().numpy()
            for token, p, label in zip(tokens, predicted_index, label_batch):
                assert len(token) == len(p)
                assert len(token) == len(label)
                predicted_index_tmp = []
                label_tmp = []
                for a, b, c in zip(token, p, label):
                    a = tokenizer.convert_ids_to_tokens([a])[0]
                    if a == "[SEP]":
                        f.write("\n")
                        break
                    predicted_index_tmp.append(b)
                    label_tmp.append(c)
                    b = label_map_reverse[b]
                    c = label_map_reverse[c]
                    f.write("{} {} {}\n".format(a, b, c))
                predicted_index_new.append(predicted_index_tmp)
                label_new.append(label_tmp)
        else:
            qids = qid_tensor.cpu().detach().numpy()
            assert len(qids) == len(predicted_index)
            for qid, p in zip(qids, predicted_index):
                f.write("{},{}\n".format(qid, p))

        label_new = label_new if len(label_new) > 0 else label_batch
        predicted_index_new = predicted_index_new if len(predicted_index_new) > 0 else predicted_index
        labels.extend(label_new)
        prediction_index_list += predicted_index_new
        del predictions
    
    f.close()
    f2.close()
    qrelf.close()
    torch.cuda.empty_cache()
    model.train()
    
    if args.data_format == "trec":
        map, mrr, p30 = evaluate_trec(predictions_file=args.output_path2, \
            qrels_file=split + '.' + args.qrels_path)
        return [["map", "mrr", "p30"],[map, mrr, p30]]
    elif args.data_format == "ontonote":
        acc, pre, rec, f1 = evaluate_ner(prediction_index_list, labels, test_dataset.label_map)
    else:
        acc, pre, rec, f1 = evaluate_classification(prediction_index_list, labels)
    return [["f1", "acc", "precision", "recall"], [f1, acc, pre, rec]]

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', default='train', help='[train, test]')
    parser.add_argument('--device', default='cuda', help='[cuda, cpu]')
    parser.add_argument('--batch_size', default=16, type=int, help='[1, 8, 16, 32]')
    parser.add_argument('--data_size', default=41579, type=int, help='[tweet2014: 41579]')
    parser.add_argument('--learning_rate', default=1e-5, type=float, help='')
    parser.add_argument('--num_train_epochs', default=3, type=int, help='')
    parser.add_argument('--data_path', default='/data/wyang/ShortTextSemanticSimilarity/data/corpora/', help='')
    parser.add_argument('--data_name', default='annotation', help='annotation or youzan_new or tweet')
    parser.add_argument('--pytorch_dump_path', default='saved.model', help='')
    parser.add_argument('--load_trained', action='store_true', default=False, help='')
    parser.add_argument('--chinese', action='store_true', default=False, help='')
    parser.add_argument('--eval_steps', default=-1, type=int, help='evaluation per [eval_steps] steps, -1 for evaluation per epoch')
    parser.add_argument('--model_type', default='BertForNextSentencePrediction', help='')
    parser.add_argument('--output_path', default='predict.tmp', help='')
    parser.add_argument('--output_path2', default='predict.trec', help='')
    parser.add_argument('--qrels_path', default='qrels.trec', help='')
    parser.add_argument('--num_labels', default=2, type=int, help='')
    parser.add_argument('--data_format', default='classification', help='[classification, trec, tweet]')
    parser.add_argument('--warmup_proportion', default=0.1, type=float, help='Proportion of training to perform linear learning rate warmup. E.g., 0.1 = 10%% of training.')
    args = parser.parse_args()
    
    if args.mode == "train":
        train(args)
    else:
        scores = test(args)
        print_scores(scores)
