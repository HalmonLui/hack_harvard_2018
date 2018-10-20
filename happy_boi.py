import pretty_midi
import numpy as np 
import torch 
import torch.nn as nn 
from torch.autograd import Variable 
import torch.utils.data as data 
import os 
import random
import sys 
import matplotlib.pyplot as plt

#import midi 
sys.path.append('/Users/sonamghosh/Documents/python_code/music_lstm/midi/')

from utils import midiread, midiwrite 


def midi_filename_to_piano_roll(midi_filename):
	midi_data = midiread(midi_filename, dt=0.3)
	piano_roll = midi_data.piano_roll.transpose()

	# Binarize pressed notes
	piano_roll[piano_roll > 0] = 1

	return piano_roll


def pad_piano_roll(piano_roll, max_length=132333, pad_value=0):
	original_piano_roll_length = piano_roll.shape[1]

	padded_piano_roll = np.zeros((88, max_length))
	padded_piano_roll[:] = pad_value

	padded_piano_roll[:, :original_piano_roll_length] = piano_roll

	return padded_piano_roll


class NotesGenerationDataset(data.Dataset):
	def __init__(self, midi_folder_path, longest_sequence_length=1491):

		self.midi_folder_path = midi_folder_path

		midi_filenames = os.listdir(midi_folder_path)

		self.longest_sequence_length = longest_sequence_length

		midi_full_filenames = map(lambda filename: os.path.join(midi_folder_path, filename),
			 					  midi_filenames)

		self.midi_full_filenames = midi_full_filenames

		if longest_sequence_length is None: 
			self.update_the_max_length()


	def update_the_max_length(self):
		"""
		Recomputes longest sequence constant of the dataset
		reads all the midi files from the midi folder and finds the max length
		"""
		sequence_lengths = map(lambda filename: midi_filename_to_piano_roll(filename).shape[1],\
								self.midi_full_filenames)

		max_length = max(sequence_lengths)

		self.longest_sequence_length = max_length


	def __len__(self):
		return len(self.midi_full_filenames)


	def __getitem__(self, index):

		midi_full_filename = self.midi_full_filenames[index]

		piano_roll = midi_filename_to_piano_roll(midi_full_filename)

		# -1 because will shift
		sequene_length = piano_roll.shape[1] - 1

		# shift by one time step
		input_sequence = piano_roll[:, :-1]
		ground_truth_sequence = piano_roll[:, 1:]

		# pad sequence so that all of them have same length
		input_sequence_padded = pad_piano_roll(input_sequence, max_length=self.longest_sequence_length)

		ground_truth_sequence_padded = pad_piano_roll(ground_truth_sequence,
						                              max_length=self.longest_sequence_length,
						                              pad_value=-100)

		input_sequence_padded = input_sequence_padded.transpose()
		ground_truth_sequence_padded = ground_truth_sequence_padded.transpose()

		return (torch.FloatTensor(input_sequence_padded),
			    torch.LongTensor(ground_truth_sequence_padded),
			    torch.LongTensor([sequene_length]))


def post_process_sequence_batch(batch_tuple):
	input_sequences, output_sequences, lengths = batch_tuple

	splitted_input_sequence_batch = input_sequences.split(split_size=1)
	splitted_output_sequence_batch = output_sequences.split(split_size=1)
	splitted_lengths_batch = lengths.split(split_size=1)

	training_data_tuples = zip(splitted_input_sequence_batch,
		                       splitted_output_sequence_batch,
		                       splitted_lengths_batch)

	training_data_tuples_sorted = sorted(training_data_tuples,
									     key=lambda p: int(p[2]),
									     reverse=True)

	splitted_input_sequence_batch, splitted_output_sequence_batch, splitted_lengths_batch = zip(*training_data_tuples_sorted)

	input_sequence_batch_sorted = torch.cat(splitted_input_sequence_batch)
	output_sequence_batch_sorted = torch.cat(splitted_output_sequence_batch)
	lengths_batch_sorted = torch.cat(splitted_lengths_batch)

	# trim overall data matrix using size of longest sequence
	input_sequence_batch_sorted = input_sequence_batch_sorted[:, :lengths_batch_sorted[0, 0], :]
	output_sequence_batch_sorted = output_sequence_batch_sorted[:, :lengths_batch_sorted[0, 0], :]

	input_sequence_batch_transposed = input_sequence_batch_sorted.transpose(0, 1)

	# pytorch api needs lengths to be list of ints
	lengths_batch_sorted_list = list(lengths_batch_sorted)
	lengths_batch_sorted_list = map(lambda x: int(x), lengths_batch_sorted_list)

	return input_sequence_batch_transposed, output_sequence_batch_sorted, lengths_batch_sorted_list



trainset = NotesGenerationDataset('./Nottingham/train/')
trainset_loader = torch.utils.data.DataLoader(trainset, batch_size=10,
						                      shuffle=True, num_workers=4, drop_last=True)

print(len(trainset), len(trainset_loader))
"""
valset = NotesGenerationDataset('./Nottingham/valid/', longest_sequence_length=None)

valset_loader = torch.utils.data.DataLoader(valset, batch_size=30, shuffle=False,
	                                        num_workers=4, drop_last=False)
"""

class RNN(nn.Module):
	def __init__(self, input_size, hidden_size, num_classes, n_layers=2):
		super(RNN, self).__init__()

		self.input_size = input_size
		self.hidden_size = hidden_size
		self.num_classes = num_classes
		self.n_layers = n_layers

		self.notes_encoder = nn.Linear(in_features=input_size, out_features=hidden_size)

		self.lstm = nn.LSTM(hidden_size, hidden_size, n_layers)

		self.logits_fc = nn.Linear(hidden_size, num_classes)


	def forward(self, input_sequences, input_sequences_lengths, hidden=None):
		batch_size = input_sequences.shape[1]
		notes_encoded = self.notes_encoder(input_sequences)

		# Run on non padded regions of batch
		packed = torch.nn.utils.rnn.pack_padded_sequence(notes_encoded, input_sequences_lengths)
		outputs, hidden = self.lstm(packed, hidden)
		outputs, output_lengths = torch.nn.utils.rnn.pad_packed_sequence(outputs)  # unpack to padded

		logits = self.logits_fc(outputs)

		logits = logits.transpose(0, 1).contiguous()

		neg_logits = (1 - logits)

		# since BCE loss doesnt support masking, use cross entropy
		binary_logits = torch.stack((logits, neg_logits), dim=3).contiguous()

		logits_flatten = binary_logits.view(-1, 2)

		return logits_flatten, hidden

"""
rnn = RNN(input_size=88, hidden_size=512, num_classes=88)

criterion = nn.CrossEntropyLoss()
criterion_val = nn.CrossEntropyLoss(size_average=False)

learning_rate = 0.001
optimizer = torch.optim.Adam(rnn.parameters(), lr=learning_rate)


from pytorch_segmentation_detection.utils.visualization import Vizlist

# create fiog , axes and binding to lists

f, (loss_axis, validation_axis) = plt.subplots(2, 1)

loss_axis.plot([], [])
validation_axis.plot([], [])

loss_list = Vizlist()
val_list = Vizlist()