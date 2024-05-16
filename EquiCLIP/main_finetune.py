import os
os.environ['KMP_DUPLICATE_LIB_OK']='True'

from clip.model import CLIP
import numpy as np
import torch
import clip
import argparse
import pytorch_lightning as pl
import torch.nn as nn
import torch.optim as optim
import logging

from tqdm import tqdm
from pkg_resources import packaging

from load_model import load_model
from finetune_utils import finetune_clip
from dataset_utils import imagenet_classes, imagenet_templates, get_labels_textprompts, get_dataloader, get_ft_dataloader
from zeroshot_weights import zeroshot_classifier
from eval_utils import eval_clip
from logging_setup import setup_logging

print("Torch version:", torch.__version__)

def main(args):
    # load model and preprocess
    model: CLIP
    model, preprocess = load_model(args)

    # get labels and text prompts
    classnames, templates = get_labels_textprompts(args)

    # get dataloader
    train_loader, eval_loader = get_ft_dataloader(args, preprocess)

    # optimizer and loss criterion
    criterion = nn.CrossEntropyLoss()
    optimizer = optim.SGD(model.parameters(), lr=args.lr, momentum=0.9)

    # create text weights for different classes
    zeroshot_weights = zeroshot_classifier(args, model, classnames, templates, save_weights='True').to(args.device)
    
    val_kwargs = {
        "data_transformations": args.data_transformations, "group_name": args.group_name,
        "device": args.device, "save_scores": args.save_scores,
    }

    train_kwargs = val_kwargs.copy()
    train_kwargs["num_iterations"] = args.iter_per_finetune
    train_kwargs["iter_print_freq"] = args.iter_print_freq
    del train_kwargs["save_scores"]

    for i in range(args.num_finetunes):
        # zeroshot prediction on validation set
        print(f"Validation accuracy!")
        logging.info(f"Validation accuracy!")
        eval_clip(args, model, zeroshot_weights, eval_loader, val=False, **val_kwargs)
        print("zeroshot prediction done")
        # finetune prediction
        logging.info(f"Model finetune step number: {i+1}/{args.num_finetunes}")
        model = finetune_clip(args, model, optimizer, criterion, zeroshot_weights, train_loader, **train_kwargs)
        print(f"Finetuning step {i} done")




if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Finetune without lambda weights for various methods")
    parser.add_argument("--seed", default=0, type=int)
    parser.add_argument("--device", default='cuda:0', type=str)
    parser.add_argument("--num_finetunes", default=8, type=int) # 10
    parser.add_argument("--iter_per_finetune", default=500, type=int) # 25 for isic2018
    parser.add_argument("--iter_print_freq", default=50, type=int)
    parser.add_argument("--logit_factor", default=1., type=float)
    parser.add_argument("--lr", default=0.000005, type=float) # 0.0003 for isic2018
    parser.add_argument("--data_transformations", default="", type=str, help=["", "flip", "rot90"])
    parser.add_argument("--group_name", default="", type=str, help=["", "flip", "rot90"])
    parser.add_argument("--method", default="vanilla", type=str, help=["vanilla", "equitune", "equizero"])
    parser.add_argument("--model_name", default="RN50", type=str, help=['RN50', 'RN101', 'RN50x4', 'RN50x16',
                                                                        'RN50x64', 'ViT-B/32', 'ViT-B/16',
                                                                        'ViT-L/14', 'ViT-L/14@336px'])
    parser.add_argument("--dataset_name", default="ImagenetV2", type=str, help=["ImagenetV2", "CIFAR100", "ISIC2018", "MNIST"])
    parser.add_argument("--verbose", action='store_true')
    parser.add_argument("--softmax", action='store_true')
    parser.add_argument("--save_scores", action='store_true')
    parser.add_argument("--undersample", action='store_true')
    parser.add_argument("--oversample", action='store_true')
    args = parser.parse_args()

    args.verbose = True

    pl.seed_everything(args.seed)
    setup_logging(args)
    main(args)

# python EquiCLIP/main_finetune.py  --dataset_name CIFAR100  --method equitune --group_name rot90 --data_transformations rot90  --model_name 'RN50'
# python EquiCLIP/main_finetune.py  --dataset_name ISIC2018  --method equitune --group_name rot90 --data_transformations rot90  --model_name 'RN50'