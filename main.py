from tqdm import tqdm
import random 
import os 
import numpy as np
import argparse

import torch

from util import *


def train(args):
    model, tokenizer = load_pretrained_model_tokenizer(args.model_type, device=args.device)
    train_data_set = load_data(args.data_path, args.data_name, args.batch_size, tokenizer, "train", args.device)
    optimizer = init_optimizer(model, args.learning_rate, args.warmup_proportion, args.num_train_epochs, len(train_data_set))
    
    model.train()
    global_step = 0
    best_score = 0
    for epoch in range(1, args.num_train_epochs+1):
        tr_loss = 0
        random.shuffle(train_data_set)
        for step, batch in enumerate(tqdm(train_data_set)):
            tokens_tensor, segments_tensor, mask_tensor, label_tensor = batch
            if args.model_type == "BertForNextSentencePrediction" or args.model_type == "BertForQuestionAnswering":
                loss = model(tokens_tensor, segments_tensor, mask_tensor, label_tensor)
            else:
                loss, logits = model(tokens_tensor, segments_tensor, mask_tensor, label_tensor)
            loss.backward()
            tr_loss += loss.item()
            optimizer.step()
            model.zero_grad()
            global_step += 1
        
        acc_dev, p1_dev = test(args, split="validate", model=model, tokenizer=tokenizer)
        print("[dev]: loss: {} acc: {}, p@1: {}".format(tr_loss, acc_dev, p1_dev))
        acc_test, p1_test = test(args, split="test", model=model)
        print("[test]: loss: {} acc: {}, p@1: {}".format(tr_loss, acc_test, p1_test))
        
        if p1_dev > best_score:
            best_score = p1_dev
            # Save pytorch-model
            model_path = os.path.join(args.pytorch_dump_path, "{}_finetuned.pt".format(args.data_name))
            print("Save PyTorch model to {}".format(model_path))
            torch.save(model.state_dict(), model_path)

    acc_test, p1_test = test(args, split="test")
    print("[test]: acc: {}, p@1: {}".format(acc_test, p1_test))

def test(args, split="test", model=None, tokenizer=None):
    if model is None:
        model_path = os.path.join(args.pytorch_dump_path, "{}_finetuned.pt".format(args.data_name))
        print("Load PyTorch model from {}".format(model_path))
        model = torch.load(model_path)
    if tokenizer is None:
        model, tokenizer = load_pretrained_model_tokenizer(args.model_type, device=args.device)
    
    model.eval()
    test_dataset = load_data(args.data_path, args.data_name, args.batch_size, tokenizer, split, args.device)
    prediction_score_list, prediction_index_list, labels = [], [], []
    
    for tokens_tensor, segments_tensor, mask_tensor, label_tensor in test_dataset:
        predictions = model(tokens_tensor, segments_tensor, mask_tensor)
        predicted_index = list(torch.argmax(predictions, dim=1).cpu().numpy())
        prediction_index_list += predicted_index
        predicted_score = list(predictions[:, 1].cpu().detach().numpy())
        prediction_score_list.extend(predicted_score)
        labels.extend(list(label_tensor.cpu().detach().numpy()))
    
    acc = get_acc(prediction_index_list, labels)
    p1 = get_p1(prediction_score_list, labels, args.data_path, args.data_name, args.split)
    return acc, p1

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', default='train', help='[train, test]')
    parser.add_argument('--device', default='cuda', help='[cuda, cpu]')
    parser.add_argument('--batch_size', default=16, type=int, help='[1, 8, 16, 32]')
    parser.add_argument('--learning_rate', default=5e-5, type=float, help='')
    parser.add_argument('--num_train_epochs', default=3, type=int, help='')
    parser.add_argument('--data_path', default='/data/wyang/ShortTextSemanticSimilarity/data/corpora/', help='')
    parser.add_argument('--data_name', default='annotation', help='annotation or youzan_new')
    parser.add_argument('--pytorch_dump_path', default='model/', help='')
    parser.add_argument('--model_type', default='BertForNextSentencePrediction', help='')
    parser.add_argument('--warmup_proportion', default=0.1, type=float, help='Proportion of training to perform linear learning rate warmup. E.g., 0.1 = 10%% of training.')
    args = parser.parse_args()
    
    if args.mode == "train":
        train(args)
    else:
        acc_test, p1_test = test(args)
        print("[test]: acc: {}, p@1: {}".format(acc_test, p1_test))
