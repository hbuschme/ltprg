import os.path as path
import datetime
import argparse
import torch
import json
import numpy as np
import ltprg.data.feature
import mung.config.feature as cfeature
import mung.config.torch_ext.learn as clearn
import ltprg.config.edit as cedit
from mung.util.config import Config
from mung.util.log import Logger
from mung.torch_ext.eval import Evaluation
from ltprg.util.file import make_indexed_dir
from ltprg.model.seq import VariableLengthNLLLoss, strs_for_scored_samples
from ltprg.model.edit import EditModelSequentialNoInput
from ltprg.data.curriculum import make_sua_datum_token_frequency_fn

parser = argparse.ArgumentParser()
parser.add_argument('id', action="store")
parser.add_argument('env', action="store")
parser.add_argument('data', action="store")
parser.add_argument('model', action="store")
parser.add_argument('learn', action="store")
parser.add_argument('train_evals', action="store")
parser.add_argument('dev_evals', action="store")
parser.add_argument('test_evals', action="store")
parser.add_argument('output_dir', action="store")
parser.add_argument('--seed', action='store', dest='seed', type=int, default=1)
parser.add_argument('--gpu', action='store', dest='gpu', type=int, default=1)
parser.add_argument('--eval_test', action='store', dest='eval_test', type=int, default=0)
parser.add_argument('--clean_length', action='store', dest='clean_length', type=int, default=12)
parser.add_argument('--only_correct_clean_train', action='store', dest='only_correct_clean_train', type=bool, default=True)
args, extra_env_args = parser.parse_known_args()
extra_env = Config.load_from_list(extra_env_args)

# Initalize current run parameters
id = args.id
gpu = bool(args.gpu)
seed = args.seed
eval_test = bool(args.eval_test)
output_dir = args.output_dir

# Load configuration files
env = Config.load(args.env).merge(extra_env)
data_config = Config.load(args.data, environment=env)
model_config = Config.load(args.model, environment=env)
learn_config = Config.load(args.learn, environment=env)
train_evals_config = Config.load(args.train_evals, environment=env)
dev_evals_config = Config.load(args.dev_evals, environment=env)
test_evals_config = Config.load(args.test_evals, environment=env)

# Random seed
if gpu:
    torch.cuda.manual_seed(seed)
torch.manual_seed(seed)
np.random.seed(seed)

# Setup data, model, and evaluations
_, data_sets = cfeature.load_mvdata(data_config)
keys = set(data_sets.keys())
for key in keys:
    if key.startswith("train") or key.startswith("dev") or key.startswith("test"):
        new_key = key + "_cleanutts"
        data_sets[new_key] = data_sets[key].filter(lambda d : len(d.get("utterances")) == 1 and len(d.get("utterances[0].nlp.clean_strs.strs")) <= args.clean_length)
        print "Created extra data set " + new_key + " of size " + str(data_sets[new_key].get_size()) + " from " + str(data_sets[key].get_size())

        if args.only_correct_clean_train and key.startswith("train"):
            data_sets[new_key] = data_sets[new_key].filter(lambda d : d.get("state.state.target") == d.get("state.state.listenerOrder")[d.get("action.action.lClicked")] )
            print "Filtered clean train data to only listener correct examples (" + str(data_sets[new_key].get_size()) + ")"

data_parameter, edit_model = cedit.load_edit_model(model_config, data_sets["train"], gpu=gpu)
train_evals = cedit.load_evaluations(train_evals_config, data_sets, gpu=gpu)
dev_evals = cedit.load_evaluations(dev_evals_config, data_sets, gpu=gpu)
test_evals = cedit.load_evaluations(test_evals_config, data_sets, gpu=gpu)

# Setup output files
output_path = make_indexed_dir(path.join(output_dir, str(id) + "_seed" + str(seed)))
log_path = path.join(output_path, "log")
results_path = path.join(output_path, "results")
test_results_path = path.join(output_path, "test_results")
model_path = path.join(output_path, "model")
samples_path = path.join(output_path, "samples")
config_output_path = path.join(output_path, "config.json")

# Output config
full_config = dict()
full_config["id"] = id
full_config["gpu"] = gpu
full_config["seed"] = seed
full_config["time"] = str(datetime.datetime.now())
full_config["eval_test"] = eval_test
full_config["output_dir"] = output_dir
full_config["env"] = env.get_dict()
full_config["data"] = data_config.get_dict()
full_config["model"] = model_config.get_dict()
full_config["learn"] = learn_config.get_dict()
full_config["train_evals"] = train_evals_config.get_dict()
full_config["dev_evals"] = dev_evals_config.get_dict()
full_config["test_evals"] = test_evals_config.get_dict()
with open(config_output_path, 'w') as fp:
    json.dump(full_config, fp)

logger = Logger()
logger.set_file_path(log_path)

# Run training 
loss_criterion = VariableLengthNLLLoss()
last_model, best_model, best_iteration = clearn.train_from_config(learn_config, \
    data_parameter, loss_criterion, logger, train_evals, edit_model, data_sets,\
    curriculum_key_fn_constructor=make_sua_datum_token_frequency_fn)

# Output logs
logger.dump(file_path=log_path)

# Output results
results_logger = Logger()
results = Evaluation.run_all(dev_evals, best_model)
results["Iteration"] = best_iteration
results_logger.log(results)
results_logger.dump(file_path=results_path)

# Output test results
if eval_test:
    test_results_logger = Logger()
    results = Evaluation.run_all(test_evals, best_model)
    results["Iteration"] = best_iteration
    test_results_logger.log(results)
    test_results_logger.dump(file_path=test_results_path)

# Get and output samples
seq_data = data_sets["dev"][data_parameter["seq"]]
seq, seq_length, _ = seq_data.get_batch(0,40)

samples = best_model.sample(seq, seq_length, n_per_input=20)
sample_strs = strs_for_scored_samples(samples, seq_data)
samples_output = "Samples:\n"
for i in range(len(sample_strs)):
    samples_output += "\n".join(sample_strs[i])
    samples_output += "\n\n"

with open(samples_path, "w") as samples_file:
    samples_file.write(samples_output)

# Output model 
best_model.save(model_path)

