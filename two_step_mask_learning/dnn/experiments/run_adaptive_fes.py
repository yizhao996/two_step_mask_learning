"""!
@brief Run an initial CometML experiment

@author Efthymios Tzinis {etzinis2@illinois.edu}
@copyright University of Illinois at Urbana-Champaign
"""

import os
import sys

sys.path.append('../../../')
from __config__ import API_KEY

from comet_ml import Experiment

import torch
from tqdm import tqdm
from pprint import pprint
import two_step_mask_learning.dnn.dataset_loader.torch_dataloader as dataloader
from __config__ import WSJ_MIX_2_8K_PREPROCESSED_EVAL_P, \
    WSJ_MIX_2_8K_PREPROCESSED_TEST_P, WSJ_MIX_2_8K_PREPROCESSED_TRAIN_P
from __config__ import WSJ_MIX_2_8K_PREPROCESSED_EVAL_PAD_P, \
    WSJ_MIX_2_8K_PREPROCESSED_TEST_PAD_P, WSJ_MIX_2_8K_PREPROCESSED_TRAIN_PAD_P
from __config__ import TIMIT_MIX_2_8K_PREPROCESSED_EVAL_P, \
    TIMIT_MIX_2_8K_PREPROCESSED_TEST_P, TIMIT_MIX_2_8K_PREPROCESSED_TRAIN_P
import two_step_mask_learning.dnn.losses.sisdr as sisdr_lib
import two_step_mask_learning.dnn.losses.norm as norm_lib
import two_step_mask_learning.dnn.models.adaptive_frontend as adaptive_fe
import two_step_mask_learning.dnn.utils.cometml_loss_report as cometml_report
import two_step_mask_learning.dnn.utils.log_audio as log_audio
import two_step_mask_learning.dnn.experiments.utils.cmd_args_parser as parser


args = parser.get_args()


hparams = {
    "train_dataset": args.train,
    "val_dataset": args.val,
    "experiment_name": args.experiment_name,
    "project_name": args.project_name,
    "afe_reg": args.adaptive_fe_regularizer,
    "n_kernel": args.n_kernel,
    "n_basis": args.n_basis,
    "bs": args.batch_size,
    "n_jobs": args.n_jobs,
    "tr_get_top": args.n_train,
    "val_get_top": args.n_val,
    "cuda_devs": args.cuda_available_devices,
    "n_epochs": args.n_epochs,
    "learning_rate": args.learning_rate,
    "return_items": args.return_items,
    "tags": args.cometml_tags,
    "log_path": args.experiment_logs_path
}

if (hparams['train_dataset'] == 'WSJ2MIX8K' and
    hparams['val_dataset'] == 'WSJ2MIX8K'):
    hparams['in_samples'] = 32000
    hparams['n_sources'] = 2
    hparams['fs'] = 8000.
    hparams['train_dataset_path'] = WSJ_MIX_2_8K_PREPROCESSED_TRAIN_P
    hparams['val_dataset_path'] = WSJ_MIX_2_8K_PREPROCESSED_EVAL_P
elif (hparams['train_dataset'] == 'WSJ2MIX8KPAD' and
    hparams['val_dataset'] == 'WSJ2MIX8KPAD'):
    hparams['in_samples'] = 32000
    hparams['n_sources'] = 2
    hparams['fs'] = 8000.
    hparams['train_dataset_path'] = WSJ_MIX_2_8K_PREPROCESSED_TRAIN_PAD_P
    hparams['val_dataset_path'] = WSJ_MIX_2_8K_PREPROCESSED_EVAL_PAD_P
elif(hparams['train_dataset'] == 'TIMITMF8K' and
     hparams['val_dataset'] == 'TIMITMF8K'):
    hparams['in_samples'] = 16000
    hparams['n_sources'] = 2
    hparams['fs'] = 8000.
    hparams['train_dataset_path'] = TIMIT_MIX_2_8K_PREPROCESSED_TRAIN_P
    hparams['val_dataset_path'] = TIMIT_MIX_2_8K_PREPROCESSED_EVAL_P
    hparams['return_items'] = ['mic1_wav_downsampled',
                               'clean_sources_wavs_downsampled']
else:
    raise NotImplementedError('Datasets: {}, {} are not available'
                              ''.format(hparams['train_dataset'],
                                        hparams['val_dataset']))
if hparams["log_path"] is not None:
    audio_logger = log_audio.AudioLogger(hparams["log_path"],
                                         hparams["fs"],
                                         hparams["bs"],
                                         hparams["n_sources"])
else:
    audio_logger = None

experiment = Experiment(API_KEY,
                        project_name=hparams['project_name'])
experiment.log_parameters(hparams)

experiment_name = '_'.join(hparams['tags'])
for tag in hparams['tags']:
    experiment.add_tag(tag)

if hparams['experiment_name'] is not None:
    experiment.set_name(hparams['experiment_name'])
else:
    experiment.set_name(experiment_name)

# define data loaders
train_gen, val_gen = dataloader.get_data_generators(
    [hparams['train_dataset_path'],
     hparams['val_dataset_path']],
    bs=hparams['bs'], n_jobs=hparams['n_jobs'],
    get_top=[hparams['tr_get_top'],
             hparams['val_get_top']],
    return_items=hparams['return_items']
)

# define the losses that are going to be used
back_loss_tr_loss_name, back_loss_tr_loss = (
    'tr_back_loss_SISDRi',
    sisdr_lib.PermInvariantSISDR(batch_size=hparams['bs'],
                                 n_sources=hparams['n_sources'],
                                 zero_mean=True,
                                 backward_loss=True,
                                 improvement=True))

val_losses = dict([
    ('val_SISDR', sisdr_lib.PermInvariantSISDR(batch_size=hparams['bs'],
                                               n_sources=hparams['n_sources'],
                                               zero_mean=True,
                                               backward_loss=False)),
    ('val_SISDRi', sisdr_lib.PermInvariantSISDR(batch_size=hparams['bs'],
                                                n_sources=hparams['n_sources'],
                                                zero_mean=True,
                                                backward_loss=False,
                                                improvement=True))
  ])

os.environ['CUDA_VISIBLE_DEVICES'] = ','.join([cad
                                               for cad in hparams['cuda_devs']])


model = adaptive_fe.AdaptiveModulatorConvAE(
    hparams['n_basis'],
    hparams['n_kernel'],
    regularizer=hparams['afe_reg'],
    n_sources=hparams['n_sources'])

numparams = 0
for f in model.parameters():
    if f.requires_grad:
        numparams += f.numel()
experiment.log_parameter('Parameters', numparams)

model.cuda()
opt = torch.optim.Adam(model.parameters(), lr=hparams['learning_rate'])
all_losses = [back_loss_tr_loss_name] + [k for k in sorted(val_losses.keys())]

tr_step = 0
val_step = 0
for i in range(hparams['n_epochs']):
    res_dic = {}
    for loss_name in all_losses:
        res_dic[loss_name] = {'mean': 0., 'std': 0., 'acc': []}
    print("Adaptive Exp: {} - {} || Epoch: {}/{}".format(experiment.get_key(),
                                                         experiment.get_tags(),
                                                         i+1,
                                                         hparams['n_epochs']))
    model.train()

    for data in tqdm(train_gen, desc='Training'):
        opt.zero_grad()
        m1wavs = data[0].unsqueeze(1).cuda()
        clean_wavs = data[-1].cuda()

        recon_sources, enc_masks = model(m1wavs, clean_wavs)
        l = back_loss_tr_loss(recon_sources,
                              clean_wavs,
                              initial_mixtures=m1wavs)
        l.backward()
        opt.step()
        res_dic[back_loss_tr_loss_name]['acc'].append(l.item())
    tr_step += 1

    if val_gen is not None:
        model.eval()
        with torch.no_grad():
            for data in tqdm(val_gen, desc='Validation'):
                m1wavs = data[0].unsqueeze(1).cuda()
                clean_wavs = data[-1].cuda()

                recon_sources, enc_masks = model(m1wavs, clean_wavs)

                AE_rec_mixture = None
                for loss_name, loss_func in val_losses.items():
                    if 'AE' in loss_name:
                        AE_rec_mixture = model.AE_recontruction(m1wavs)
                        l = loss_func(AE_rec_mixture,
                                      m1wavs)
                    else:
                        l = loss_func(recon_sources,
                                      clean_wavs,
                                      initial_mixtures=m1wavs)
                    res_dic[loss_name]['acc'].append(l.item())
            if audio_logger is not None:
                audio_logger.log_batch(recon_sources,
                                       clean_wavs,
                                       m1wavs,
                                       mixture_rec=AE_rec_mixture)
        val_step += 1

    res_dic = cometml_report.report_losses_mean_and_std(res_dic,
                                                        experiment,
                                                        tr_step,
                                                        val_step)
    for loss_name in res_dic:
        res_dic[loss_name]['acc'] = []
    pprint(res_dic)
