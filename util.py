from tqdm import tqdm
import random 
import os
import numpy as np
import subprocess
import shlex
import sys

import torch

from pytorch_pretrained_bert import BertTokenizer, BertModel, BertForMaskedLM, BertForSequenceClassification, BertForNextSentencePrediction
from pytorch_pretrained_bert.optimization import BertAdam


def load_pretrained_model_tokenizer(model_type="BertForSequenceClassification", device="cuda", chinese=False):
    # Load pre-trained model (weights)
    if chinese:
        base_model = "bert-base-chinese"
    else:
        base_model = "bert-base-uncased"
    if model_type == "BertForSequenceClassification":
        model = BertForSequenceClassification.from_pretrained(base_model)
        # Load pre-trained model tokenizer (vocabulary)
    elif model_type == "BertForNextSentencePrediction":
        model = BertForNextSentencePrediction.from_pretrained(base_model)
    else:
        print("[Error]: unsupported model type")
        return None, None
    
    tokenizer = BertTokenizer.from_pretrained(base_model)
    model.to(device)
    return model, tokenizer

class DataGenerator(object):
    def __init__(self, data_path, data_name, batch_size, tokenizer, split, device="cuda", data_format="trec", add_url=False):
        super(DataGenerator, self).__init__()
        self.data = []
        if data_format == "trec":
            self.fa = open(os.path.join(data_path, "{}/{}/a.toks".format(data_name, split)))
            self.fb = open(os.path.join(data_path, "{}/{}/b.toks".format(data_name, split)))
            self.fsim = open(os.path.join(data_path, "{}/{}/sim.txt".format(data_name, split)))
            self.fid = open(os.path.join(data_path, "{}/{}/id.txt".format(data_name, split)))
            if add_url:
                self.furl = open(os.path.join(data_path, "{}/{}/url.txt".format(data_name, split)))
                for a, b, sim, ID, url in zip(self.fa, self.fb, self.fsim, self.fid, self.furl):
                    self.data.append([sim.replace("\n", ""), a.replace("\n", ""), b.replace("\n", ""), \
                            ID.replace("\n", ""), url.replace("\n", "")])
            else:
                for a, b, sim, ID in zip(self.fa, self.fb, self.fsim, self.fid):
                    self.data.append([sim.replace("\n", ""), a.replace("\n", ""), b.replace("\n", ""), \
                            ID.replace("\n", "")])

        else:
            self.f = open(os.path.join(data_path, "{}/{}_{}.csv".format(data_name, data_name, split)))
            for l in self.f:
                ls = l.replace("\n", "").split("\t")
                if len(ls) == 3:
                    self.data.append(ls)
                else:
                    self.data.append([ls[0], ls[1], " ".join(ls[2:])])
        
        np.random.shuffle(self.data)
        self.i = 0
        self.data_size = len(self.data)
        self.add_url = add_url
        self.batch_size = batch_size
        self.device = device
        self.tokenizer = tokenizer
        self.start = True

    def get_instance(self):
        ret = self.data[self.i % self.data_size]
        self.i += 1
        return ret

    def epoch_end(self):
        return self.i % self.data_size == 0

    def tokenize_index(self, text):
        tokenized_text = self.tokenizer.tokenize(text)
        # Convert token to vocabulary indices
        indexed_tokens = self.tokenizer.convert_tokens_to_ids(tokenized_text)
        return indexed_tokens

    def load_batch(self):
        test_batch, testqid_batch, mask_batch, label_batch, qid_batch, docid_batch = [], [], [], [], [], []
        while True:
            if not self.start and self.epoch_end():
                self.start = True
                break
            self.start = False
            instance = self.get_instance()
            if len(instance) == 5:
                label, a, b, ID, url = instance
            elif len(instance) == 4:
                label, a, b, ID = instance
            else:
                label, a, b = instance
            a = "[CLS] " + a + " [SEP]"
            if self.add_url:
                b = b + " " + url + " [SEP]"
            else:
                b = b + " [SEP]"
            a_index = self.tokenize_index(a)
            b_index = self.tokenize_index(b)
            combine_index = a_index + b_index
            segments_ids = [0] * len(a_index) + [1] * len(b_index)
            test_batch.append(torch.tensor(combine_index))
            testqid_batch.append(torch.tensor(segments_ids))
            mask_batch.append(torch.ones(len(combine_index)))
            label_batch.append(int(label))
            if len(instance) >= 4:
                qid, _, docid, _, _, _ = ID.split()
                qid = int(qid)
                docid = int(docid)
                qid_batch.append(qid)
                docid_batch.append(docid)
            if len(test_batch) >= self.batch_size or self.epoch_end():
                # Convert inputs to PyTorch tensors
                tokens_tensor = torch.nn.utils.rnn.pad_sequence(test_batch, batch_first=True, padding_value=0).to(self.device)
                segments_tensor = torch.nn.utils.rnn.pad_sequence(testqid_batch, batch_first=True, padding_value=0).to(self.device)
                mask_tensor = torch.nn.utils.rnn.pad_sequence(mask_batch, batch_first=True, padding_value=0).to(self.device)
                label_tensor = torch.tensor(label_batch, device=self.device)
                if len(instance) >= 4:
                    qid_tensor = torch.tensor(qid_batch, device=self.device)
                    docid_tensor = torch.tensor(docid_batch, device=self.device)
                    return (tokens_tensor, segments_tensor, mask_tensor, label_tensor, qid_tensor, docid_tensor)
                else:
                    return (tokens_tensor, segments_tensor, mask_tensor, label_tensor)

                test_batch, testqid_batch, mask_batch, label_batch, qid_batch, docid_batch = [], [], [], [], [], []
 
        return None 


def init_optimizer(model, learning_rate, warmup_proportion, num_train_epochs, data_size, batch_size):
    param_optimizer = list(model.named_parameters())
    no_decay = ['bias', 'gamma', 'beta']
    num_train_steps = data_size / batch_size * num_train_epochs
    optimizer_grouped_parameters = [
        {'params': [p for n, p in param_optimizer if n not in no_decay], 'weight_decay_rate': 0.01},
        {'params': [p for n, p in param_optimizer if n in no_decay], 'weight_decay_rate': 0.0}
        ]

    optimizer = BertAdam(optimizer_grouped_parameters,
                    lr=learning_rate,
                    warmup=warmup_proportion,
                    t_total=num_train_steps)
    
    return optimizer
        
def evaluate_trec(predictions_file, qrels_file):
    pargs = shlex.split("/bin/sh run_eval.sh '{}' '{}'".format(qrels_file, predictions_file))
    p = subprocess.Popen(pargs, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    pout, perr = p.communicate()

    if sys.version_info[0] < 3:
        lines = pout.split(b'\n')
    else:
        lines = pout.split(b'\n')
    map = float(lines[0].strip().split()[-1])
    mrr = float(lines[1].strip().split()[-1])
    p30 = float(lines[2].strip().split()[-1])
    return map, mrr, p30

def evaluate_classification(prediction_index_list, labels):
    acc = get_acc(prediction_index_list, labels)
    pre, rec, f1 = get_pre_rec_f1(prediction_index_list, labels)
    return acc, pre, rec, f1

def get_acc(prediction_index_list, labels):
    acc = sum(np.array(prediction_index_list) == np.array(labels))
    return acc / (len(labels) + 1e-9)

def get_pre_rec_f1(prediction_index_list, labels):
    tp, tn, fp, fn = 0, 0, 0, 0
    # print("prediction_index_list: ", prediction_index_list)
    # print("labels: ", labels)
    assert len(prediction_index_list) == len(labels)
    for p, l in zip(prediction_index_list, labels):
        if p == l:
            if p == 1:
                tp += 1
            else:
                tn += 1
        else:
            if p == 1:
                fp += 1
            else:
                fn += 1
    eps = 1e-8
    precision = tp * 1.0 / (tp + fp + eps)
    recall = tp * 1.0 / (tp + fn + eps)
    f1 = 2 * precision * recall / (precision + recall + eps)
    return precision, recall, f1

def get_p1(prediction_score_list, labels, data_path, data_name, split):
    f = open(os.path.join(data_path, "{}/{}_{}.csv".format(data_name, data_name, split)))
    a2score_label = {}
    for line, p, l in zip(f, prediction_score_list, labels):
        label, a, b = line.replace("\n", "").split("\t")
        if a not in a2score_label:
            a2score_label[a] = []
        a2score_label[a].append((p, l))
    
    acc = 0
    no_true = 0
    for a in a2score_label:
        a2score_label[a] = sorted(a2score_label[a], key=lambda x: x[0], reverse=True)
        if a2score_label[a][0][1] > 0:
            acc += 1
        if sum([tmp[1] for tmp in a2score_label[a]]) == 0:
            no_true += 1

    p1 = acc / (len(a2score_label) - no_true)
    
    return p1
