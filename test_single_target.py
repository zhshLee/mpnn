import cPickle as pickle
import sys

import numpy as np
import torch
import torch.cuda
from rdkit import Chem
from sklearn import metrics
from sklearn.model_selection import train_test_split
from torch import nn
from torch import optim
from torch.utils.data import DataLoader

from models.basic_model import BasicModel
from models.graph_model_wrapper import GraphWrapper
from mol_graph import *
from mol_graph import GraphEncoder
from pre_process.data_loader import GraphDataSet, collate_2d_graphs
from pre_process.load_dataset import load_classification_dataset


def count_model_params(model):
    model_parameters = filter(lambda p: p.requires_grad, model.parameters())
    return sum([np.prod(p.size()) for p in model_parameters])


def save_model(model, model_att, label):
    # type: (nn.Module, dict) -> None
    torch.save(model.state_dict(), 'basic_model_'+label+'.state_dict')
    with open('basic_model_attributes_'+label+'.pickle', 'wb') as out:
        pickle.dump(model_att, out)


seed = 317
torch.manual_seed(seed)
data_file = sys.argv[1]

mgf = MolGraphFactory(Mol2DGraph.TYPE, AtomFeatures(), BondFeatures())
try:
    file_data = np.load(data_file+'.npz')
    data = file_data['data']
    no_labels = int(file_data['no_labels'])
    all_labels = file_data['all_labels']
    file_data.close()
except IOError:
    data, no_labels, all_labels = load_classification_dataset(data_file+'.csv',
                                              'InChI', Chem.MolFromInchi, mgf, 'target')
    graph_encoder = GraphEncoder()
    with open('basic_model_graph_encoder.pickle', 'wb') as out:
        pickle.dump(graph_encoder, out)
    np.savez_compressed(data_file, data=data, no_labels=no_labels, all_labels=all_labels)

model_attributes = {
    'afm': data[0].afm.shape[-1],
    'bfm': data[0].bfm.shape[-1],
    'mfm': data[0].afm.shape[-1],
    'adj': data[0].adj.shape[-1],
    'out': 4*data[0].afm.shape[-1],
    'classification_output': 2
}

model = nn.Sequential(
    GraphWrapper(BasicModel(model_attributes['afm'], model_attributes['bfm'], model_attributes['mfm'],
                            model_attributes['adj'], model_attributes['out'])),
    nn.BatchNorm1d(model_attributes['out']),
    nn.Linear(model_attributes['out'], model_attributes['classification_output'])
)

selected_label = np.random.choice(np.arange(no_labels))
print "Target selected: {}".format(selected_label)

for graph in data:
    graph.label = int(selected_label == graph.label)

print "Model has: {} parameters".format(count_model_params(model))
device = 'cpu'
if torch.cuda.is_available():
    device = 'cuda'

weights = torch.Tensor([len(all_labels) - np.count_nonzero(selected_label != all_labels),
                        len(all_labels) - np.count_nonzero(selected_label == all_labels)])
model.to(device)
criterion = nn.CrossEntropyLoss(weights.to(device))
optimizer = optim.Adam(model.parameters())
model.train()

train, test, train_labels, test_labels = train_test_split(data, all_labels, test_size=0.1,
                                                          random_state=seed, stratify=all_labels)
del data
del all_labels
del test_labels
train, val = train_test_split(train, test_size=0.1, random_state=seed, stratify=train_labels)
del train_labels
train = GraphDataSet(train)
val = GraphDataSet(val)
test = GraphDataSet(test)
train = DataLoader(train, 16, shuffle=True, collate_fn=collate_2d_graphs)
val = DataLoader(val, 16, shuffle=True, collate_fn=collate_2d_graphs)
test = DataLoader(test, 16, shuffle=True, collate_fn=collate_2d_graphs)

losses = []
epoch_losses = []
break_con = False
for epoch in xrange(500):
    epoch_loss = 0
    for batch in train:
        model.zero_grad()
        loss = criterion(model(batch), batch['labels'])
        losses.append(loss.item())
        epoch_loss += loss.item()
        loss.backward()
        optimizer.step()
    epoch_losses.append(epoch_loss)
    if 0 == (epoch+1) % 50:
        print "epoch: {}, loss: {}".format(epoch, epoch_loss)
    break_con = loss.item() < 0.02
    if break_con:
        break

save_model(model, model_attributes)

model.eval()
labels = []
true_labels = []
for batch in val:
    labels = labels + model(batch).max(dim=-1)[1].cpu().data.numpy().tolist()
    true_labels = true_labels + batch['labels'].cpu().data.numpy().tolist()

print "accuracy: {}, precision: {}, recall: {}".format(
    metrics.accuracy_score(true_labels, labels),
    metrics.precision_score(true_labels, labels, average='binary'),
    metrics.recall_score(true_labels, labels, average='binary')
)
