import torch
import torch.nn as nn
from torch.autograd import Variable
from ltprg.model.dist import Categorical
from ltprg.model.seq import SamplingMode, SequenceModel
from ltprg.model.rsa import DistributionType, DataParameter

class PriorInputMode:
    IGNORE_TRUE_WORLD = "IGNORE_TRUE_WORLD"
    ONLY_TRUE_WORLD = "ONLY_TRUE_WORLD"

class UniformIndexPriorFn(nn.Module):
    def __init__(self, size, on_gpu=False, unnorm=False):
        super(UniformIndexPriorFn, self).__init__()
        self._size = size
        self._on_gpu = on_gpu
        self._unnorm = unnorm

    def on_gpu(self):
        return self._on_gpu

    def forward(self, observation):
        vs = torch.arange(0,self._size).unsqueeze(0).repeat(observation.size(0),1)
        if self.on_gpu():
            vs = vs.cuda()
        return Categorical(Variable(vs, requires_grad=False), on_gpu=self.on_gpu(), unnorm=self._unnorm)

    # NOTE: This assumes that all values in vs are indices that fall within
    # the range of the support
    def get_index(self, vs, observation, support, preset_batch=False):
        return vs.data.long().squeeze(), False, None

    def set_data_batch(self, batch, data_parameters):
        pass

class MultiLayerIndexPriorFn(nn.Module):
    def __init__(self, size, observation_size, depth, on_gpu=False, unnorm=False, dropout=0.5):
        super(MultiLayerIndexPriorFn, self).__init__()
        self._size = size
        self._depth = depth
        self._on_gpu = on_gpu
        self._unnorm = unnorm
        self._observation_size = observation_size

        self._nl = nn.Tanh()
        self._softmax = nn.Softmax()

        layers = []
        drops = []
        if self._depth > 1:
            for i in range(self._depth - 1):
                layers.append(nn.Linear(observation_size, observation_size))
                drops.append(nn.Dropout(dropout))
        layers.append(nn.Linear(observation_size, size))
        self._layers = nn.ModuleList(layers)
        self._drops = nn.ModuleList(drops)

    def on_gpu(self):
        return self._on_gpu

    def forward(self, observation):
        cur = observation
        if self._depth > 1:
            for i in range(self._depth - 1):
                cur = self._drops[i](self._nl(self._layers[i](cur)))
        ps = self._softmax(self._layers[self._depth - 1](cur))
        vs = torch.arange(0,self._size).unsqueeze(0).repeat(observation.size(0),1)
        if self.on_gpu():
            vs = vs.cuda()
        return Categorical(vs, ps=ps, on_gpu=self.on_gpu(), unnorm=self._unnorm)

    # NOTE: This assumes that all values in vs are indices that fall within
    # the range of the support
    def get_index(self, vs, observation, support, preset_batch=False):
        return vs.data.long().squeeze(), False, None

    def set_data_batch(self, batch, data_parameters):
        pass

class SequenceSamplingPriorFn(nn.Module):
    def __init__(self, model, input_size, training_mode=SamplingMode.FORWARD, eval_mode=SamplingMode.FORWARD, samples_per_input=1, uniform=True, seq_length=15, dist_type=DistributionType.S, heuristic=None, n_before_heuristic=20, training_input_mode=None, sample_length=15):
        super(SequenceSamplingPriorFn, self).__init__()
        self._model = model
        self._input_size = input_size
        self._training_mode=training_mode
        self._eval_mode=eval_mode
        self._samples_per_input = samples_per_input
        self._uniform = uniform
        self._seq_length = seq_length

        self._fixed_input = None
        self._fixed_seq = None
        self._ignored_input = None
        self._dist_type = dist_type
        self._heuristic = heuristic
        self._n_before_heuristic = n_before_heuristic
        self._training_input_mode = training_input_mode
        self._sample_length = sample_length

        if not uniform:
            raise ValueError("Non-uniform sequence prior not implemented")

    def on_gpu(self):
        return next(self.parameters()).is_cuda

    def set_ignored_input(self, ignored_input):
        self._fixed_input = None
        self._ignored_input = ignored_input

    def set_fixed_input(self, fixed_input):
        self._ignored_input = None
        self._fixed_input = fixed_input

    def set_fixed_seq(self, seq=None, length=None):
        if seq is None or length is None:
            self._fixed_seq = None
        else:
            self._fixed_seq = (seq.transpose(0,1), length)

    def set_samples_per_input(self, samples_per_input):
        self._samples_per_input = samples_per_input

    def forward(self, observation):
        batch_size = observation.size(0)
        inputs_per_observation = observation.size(1)/self._input_size
        all_inputs = None
        all_input_indices = None
        all_contexts = None
        if self._fixed_input is not None:
            all_inputs = observation.view(batch_size*inputs_per_observation, self._input_size)
            fixed_input_offset = torch.arange(0, batch_size).long()*inputs_per_observation + self._fixed_input.long()
            if self.on_gpu():
                fixed_input_offset = fixed_input_offset.cuda()
            all_inputs = all_inputs[fixed_input_offset]
            inputs_per_observation = 1

            if self._heuristic is not None:
                all_input_indices = self._fixed_input.long()
                all_contexts = observation
        elif self._ignored_input is not None:
            all_inputs = Variable(torch.zeros((inputs_per_observation - 1)*batch_size, self._input_size), requires_grad=False)
            obs_inputs = observation.view(batch_size, inputs_per_observation, self._input_size)
            all_index = 0

            if self._heuristic is not None:
                all_input_indices = torch.zeros((inputs_per_observation - 1)*batch_size).long()
                all_contexts = Variable(torch.zeros((inputs_per_observation - 1)*batch_size, observation.size(1)), requires_grad=False)
                if self.on_gpu():
                    all_contexts = all_contexts.cuda()

            for i in range(batch_size):
                ignored_i = self._ignored_input[i]
                for j in range(inputs_per_observation):
                    if j != ignored_i:
                        all_inputs[all_index] = obs_inputs[i,j]

                        if self._heuristic is not None:
                            all_input_indices[all_index] = j
                            all_contexts[all_index] = observation[i]

                        all_index += 1

            inputs_per_observation = observation.size(1)/self._input_size - 1
        else:
            all_inputs = observation.view(batch_size*inputs_per_observation, self._input_size)
            if self._heuristic is not None:
                all_input_indices = torch.arange(0, inputs_per_observation).unsqueeze(0).expand(batch_size, inputs_per_observation).contiguous().view(-1).long()
                all_contexts = observation.unsqueeze(1).expand(batch_size,inputs_per_observation,observation.size(1)).contiguous().view(batch_size*inputs_per_observation, observation.size(1))

        if self._heuristic is not None and self.on_gpu():
            all_input_indices = all_input_indices.cuda()

        samples = None
        if (self.training and self._training_mode == SamplingMode.FORWARD) or ((not self.training) and self._eval_mode == SamplingMode.FORWARD):
            samples = self._model.sample(n_per_input=self._samples_per_input, max_length=self._sample_length, input=all_inputs, heuristic=self._heuristic, \
                                         context=(all_contexts, all_input_indices), n_before_heuristic=self._n_before_heuristic)
        elif (self.training and self._training_mode == SamplingMode.BEAM) or ((not self.training) and self._eval_mode == SamplingMode.BEAM):
            samples = self._model.beam_search(beam_size=self._samples_per_input, max_length=self._sample_length, input=all_inputs, heuristic=self._heuristic, context=(all_contexts, all_input_indices))
        elif (self.training and self._training_mode == SamplingMode.SMC) or ((not self.training) and self._eval_mode == SamplingMode.SMC):
            samples = self._model.smc(n_per_input=self._samples_per_input, max_length=self._sample_length, input=all_inputs, heuristic=self._heuristic, \
                                         context=(all_contexts, all_input_indices))
        elif (self.training and self._training_mode == SamplingMode.BEAM_SAMPLE) or ((not self.training) and self._eval_mode == SamplingMode.BEAM_SAMPLE):
            samples = self._model.beam_sample(n_per_input=self._samples_per_input, max_length=self._sample_length, input=all_inputs, heuristic=self._heuristic, \
                                         context=(all_contexts, all_input_indices), n_before_heuristic=self._n_before_heuristic)

        has_fixed = 0
        if self._fixed_seq is not None:
            has_fixed = 1

        seq_supp_batch = Variable(torch.zeros(batch_size, self._samples_per_input * inputs_per_observation + has_fixed, self._seq_length).long(), requires_grad=False)
        length_supp_batch = torch.zeros(batch_size, self._samples_per_input * inputs_per_observation + has_fixed).long()

        if self.on_gpu():
            seq_supp_batch = seq_supp_batch.cuda()

        for i in range(batch_size):
            if self._fixed_seq is not None:
                seq_supp_batch[i,0,:] = self._fixed_seq[0][i]
                length_supp_batch[i,0] = self._fixed_seq[1][i]

            for j in range(inputs_per_observation):
                seqs, lengths, scores = samples[i*inputs_per_observation+j]
                seqs = Variable(seqs)
                seq_supp_batch[i, (has_fixed+j*self._samples_per_input):(has_fixed+(j+1)*self._samples_per_input), 0:seqs.size(0)] = seqs.transpose(0,1)
                length_supp_batch[i, (has_fixed+j*self._samples_per_input):(has_fixed+(j+1)*self._samples_per_input)] = lengths

        return Categorical((seq_supp_batch, length_supp_batch), on_gpu=self.on_gpu())

    def get_index(self, seq_with_len, observation, support, preset_batch=False):
        if preset_batch:
            index = torch.zeros(seq_with_len[0].size(0)).long()
            if self.on_gpu():
                index = index.cuda()
            return index, False, None
        else:
            return Categorical.get_support_index(seq_with_len, support)

    def set_data_batch(self, batch, data_parameters):
        seqType = DataParameter.UTTERANCE
        inputType = DataParameter.WORLD
        if self._dist_type == DistributionType.L:
            seqType == DataParameter.WORLD
            inputType = DataParameter.UTTERANCE

        if self.training:
            if self._training_input_mode == PriorInputMode.IGNORE_TRUE_WORLD:
                self.set_ignored_input(batch[data_parameters[inputType]].squeeze())
            elif self._training_input_mode == PriorInputMode.ONLY_TRUE_WORLD:
                self.set_fixed_input(batch[data_parameters[inputType]].squeeze())
        else:
            self.set_fixed_seq(seq=None, length=None)
            self.set_ignored_input(None)

        # NOTE: If dist type != mode, this means that
        # for example, the L model is running with an utterance prior
        # that should include the observed utterance
        if self.training or self._dist_type != data_parameters.get_mode():
            seq, length, mask = batch[data_parameters[seqType]]
            if self.on_gpu():
                seq = seq.cuda()
            self.set_fixed_seq(seq=Variable(seq), length=length)



class EditSamplingPriorFn(nn.Module):
    def __init__(self, model, input_size, samples_per_input=1, seq_length=15, dist_type=DistributionType.S, heuristic=None, n_before_heuristic=20):
        super(EditSamplingPriorFn, self).__init__()
        self._model = model
        self._input_size = input_size
        self._samples_per_input = samples_per_input
        self._seq_length = seq_length

        self._fixed_seq = None
        self._dist_type = dist_type
        self._heuristic = heuristic
        self._n_before_heuristic = n_before_heuristic

    def on_gpu(self):
        return next(self.parameters()).is_cuda

    def set_samples_per_input(self, samples_per_input):
        self._samples_per_input = samples_per_input

    def set_fixed_seq(self, seq=None, length=None):
        if seq is None or length is None:
            self._fixed_seq = None
        else:
            self._fixed_seq = (seq.transpose(0,1), length)

    def forward(self, observation):
        batch_size = observation.size(0)
        inputs_per_observation = observation.size(1)/self._input_size
        
        all_input_indices = None
        all_contexts = None
        all_inputs = observation.view(batch_size*inputs_per_observation, self._input_size)
        if self._heuristic is not None:
            all_input_indices = torch.arange(0, inputs_per_observation).unsqueeze(0).expand(batch_size, inputs_per_observation).contiguous().view(-1).long()
            all_contexts = observation.unsqueeze(1).expand(batch_size,inputs_per_observation,observation.size(1)).contiguous().view(batch_size*inputs_per_observation, observation.size(1))
            if self.on_gpu():
                all_input_indices = all_input_indices.cuda()

        seq = self._fixed_seq[0].transpose(0,1).unsqueeze(2).repeat(1,1,inputs_per_observation).view(self._fixed_seq[0].size(1), -1)
        seq_length = self._fixed_seq[1].unsqueeze(1).repeat(1,inputs_per_observation).view(-1)

        samples = self._model.sample(seq, seq_length, n_per_input=self._samples_per_input, \
                                        input=all_inputs, heuristic=self._heuristic, \
                                        context=(all_contexts, all_input_indices), n_before_heuristic=self._n_before_heuristic)

        seq_supp_batch = Variable(torch.zeros(batch_size, self._samples_per_input * inputs_per_observation + 1, self._seq_length).long(), requires_grad=False)
        length_supp_batch = torch.zeros(batch_size, self._samples_per_input * inputs_per_observation + 1).long()

        if self.on_gpu():
            seq_supp_batch = seq_supp_batch.cuda()

        for i in range(batch_size):
            if self._fixed_seq is not None:
                seq_supp_batch[i,0,:] = self._fixed_seq[0][i]
                length_supp_batch[i,0] = self._fixed_seq[1][i]

            for j in range(inputs_per_observation):
                seqs, lengths, _ = samples[i*inputs_per_observation+j]
                seqs = Variable(seqs)
                seq_supp_batch[i, (1+j*self._samples_per_input):(1+(j+1)*self._samples_per_input), 0:seqs.size(0)] = seqs.transpose(0,1)
                length_supp_batch[i, (1+j*self._samples_per_input):(1+(j+1)*self._samples_per_input)] = lengths

        return Categorical((seq_supp_batch, length_supp_batch), on_gpu=self.on_gpu())

    def get_index(self, seq_with_len, observation, support, preset_batch=False):
        if preset_batch:
            index = torch.zeros(seq_with_len[0].size(0)).long()
            if self.on_gpu():
                index = index.cuda()
            return index, False, None
        else:
            return Categorical.get_support_index(seq_with_len, support)

    def set_data_batch(self, batch, data_parameters):
        seqType = DataParameter.UTTERANCE
        if self._dist_type == DistributionType.L:
            seqType == DataParameter.WORLD

        # NOTE: If dist type != mode, this means that
        # for example, the L model is running with an utterance prior
        # that should include the observed utterance
        if self.training or self._dist_type != data_parameters.get_mode():
            seq, length, _ = batch[data_parameters[seqType]]
            if self.on_gpu():
                seq = seq.cuda()
            self.set_fixed_seq(seq=Variable(seq), length=length)
