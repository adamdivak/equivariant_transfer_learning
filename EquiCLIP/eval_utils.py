import logging
import os
import torch
import torch.nn.functional as F

from tqdm import tqdm

from weighted_equitune_utils import compute_logits
from exp_utils import group_transform_images, random_transformed_images


group_sizes = {"rot90": 4., "flip": 2., "": 1.}


def accuracy(output, target, topk=(1,)):
    pred = output.topk(max(topk), 1, True, True)[1].t()  # dim [max_topk, batch_size]
    correct = pred.eq(target.view(1, -1).expand_as(pred))
    return [float(correct[:k].reshape(-1).float().sum(0, keepdim=True).cpu().numpy()) for k in topk]


def equi0_accuracy(output, target, topk=(1,), group_name=""):
    if group_name == "":
      pred = output.topk(max(topk), 1, True, True)[1].t()  # dim [max_topk, batch_size]
      correct = pred.eq(target.view(1, -1).expand_as(pred))  # dim [max_topk, batch_size]
      return [float(correct[:k].reshape(-1).float().sum(0, keepdim=True).cpu().numpy()) for k in topk]
    elif group_name == "rot90":
      group_size = 4
      output_shape = output.shape
      output = output.reshape(group_size, output_shape[0] // group_size, output_shape[1])  # [group_size, batch_size, num_classes]
      # pred_values, pred_indices = output.topk(max(topk), 2, True, True)[0],\
      #                             output.topk(max(topk), 2, True, True)[1]  # dim [group_size, batch_size, max_topk]
      # correct = pred_indices[0].t().eq(target.view(1, -1).expand_as(pred_indices[0].t()))  # dim [max_topk, group_size * batch_size]
      # return [float(correct[:k].reshape(-1).float().sum(0, keepdim=True).cpu().numpy()) for k in topk]
      output, _ = torch.max(output, dim=0, keepdim=False)  # [batch_size, num_classes]
      pred_values, pred_indices = output.topk(max(topk), 1, True, True)[0].t(), \
                                  output.topk(max(topk), 1, True, True)[1].t()  # dim [max_topk, group_size, batch_size]
      correct = pred_indices.eq(target.view(1, -1).expand_as(pred_indices))  # dim [max_topk, group_size * batch_size]
      return [float(correct[:k].reshape(-1).float().sum(0, keepdim=True).cpu().numpy()) for k in topk]
    elif group_name == "flip":
      group_size = 2
      output_shape = output.shape
      output = output.reshape(group_size, output_shape[0] // group_size, output_shape[1])  # [group_size, batch_size, num_classes]
      # pred_values, pred_indices = output.topk(max(topk), 2, True, True)[0],\
      #                             output.topk(max(topk), 2, True, True)[1]  # dim [group_size, batch_size, max_topk]
      # correct = pred_indices[0].t().eq(target.view(1, -1).expand_as(pred_indices[0].t()))  # dim [max_topk, group_size * batch_size]
      # return [float(correct[:k].reshape(-1).float().sum(0, keepdim=True).cpu().numpy()) for k in topk]
      output, _ = torch.max(output, dim=0, keepdim=False)  # [batch_size, num_classes]
      pred_values, pred_indices = output.topk(max(topk), 1, True, True)[0].t(), \
                                  output.topk(max(topk), 1, True, True)[1].t()  # dim [max_topk, group_size, batch_size]
      correct = pred_indices.eq(target.view(1, -1).expand_as(pred_indices))  # dim [max_topk, group_size * batch_size]
      return [float(correct[:k].reshape(-1).float().sum(0, keepdim=True).cpu().numpy()) for k in topk]
    else:
      raise NotImplementedError


def equitune_accuracy(output, target, topk=(1,), group_name=""):
    if group_name == "":
        pred = output.topk(max(topk), 1, True, True)[1].t()  # dim [max_topk, batch_size]
        correct = pred.eq(target.view(1, -1).expand_as(pred))  # dim [max_topk, batch_size]
        return [float(correct[:k].reshape(-1).float().sum(0, keepdim=True).cpu().numpy()) for k in topk]
    elif group_name=="rot90":
        group_size = 4
        output_shape = output.shape
        output = output.reshape(group_size, output_shape[0] // group_size, output_shape[1])  # [group_size, batch_size, num_classes]
        output = torch.mean(output, dim=0, keepdim=False)  # [batch_size, num_classes]
        pred_values, pred_indices = output.topk(max(topk), 1, True, True)[0].t(),\
                                  output.topk(max(topk), 1, True, True)[1].t()  # dim [max_topk, group_size, batch_size]
        correct = pred_indices.eq(target.view(1, -1).expand_as(pred_indices))  # dim [max_topk, group_size * batch_size]
        return [float(correct[:k].reshape(-1).float().sum(0, keepdim=True).cpu().numpy()) for k in topk]
    elif group_name == "flip":
        group_size = 2
        output_shape = output.shape
        output = output.reshape(group_size, output_shape[0] // group_size, output_shape[1])  # [group_size, batch_size, num_classes]
        output = torch.mean(output, dim=0, keepdim=False)  # [batch_size, num_classes]
        pred_values, pred_indices = output.topk(max(topk), 1, True, True)[0].t(),\
                                  output.topk(max(topk), 1, True, True)[1].t()  # dim [max_topk, group_size, batch_size]
        correct = pred_indices.eq(target.view(1, -1).expand_as(pred_indices))  # dim [max_topk, group_size * batch_size]
        return [float(correct[:k].reshape(-1).float().sum(0, keepdim=True).cpu().numpy()) for k in topk]
    else:
        raise NotImplementedError


def eval_clip(args, model, zeroshot_weights, loader, data_transformations="", group_name="", device="cuda:0",
              feature_combination_module=None, val=False, model_=None, save_scores=False):
    import time
    since = time.time()
    with torch.no_grad():
        top1, top5, n = 0., 0., 0.
        image_features_ = None
        for i, (images, target) in enumerate(tqdm(loader)):
            if val and i == 50:
                break
            images = images.to(device)  # dim [batch_size, c_in, H, H]
            images = random_transformed_images(images, data_transformations=data_transformations)  # randomly transform data

            # images = torch.rot90(images, k=1, dims=(-2, -1))
            group_images = group_transform_images(images,
                                                  group_name=group_name)  # dim [group_size, batch_size, c_in, H, H]
            group_images_shape = group_images.shape

            # dim [group_size * batch_size, c_in, H, H]
            group_images = group_images.reshape(group_images_shape[0] * group_images_shape[1], group_images_shape[2],
                                                group_images_shape[3], group_images_shape[3])
            # print(f"images.shape: {images.shape}")
            target = target.to(device)
            # print(f"target.shape: {target.shape}")

            # predict
            image_features = model.encode_image(group_images)  # dim [group_size * batch_size, feat_size=512]
            if not model_ is None:
                image_features_ = model_.encode_image(group_images)  # dim [group_size * batch_size, feat_size=512]

            # print(f"image_features.shape: {image_features.shape}")
            image_features /= image_features.norm(dim=-1, keepdim=True)

            if not model_ is None:
                image_features_norm_ = image_features_.clone().norm(dim=-1, keepdim=True)
                image_features_ = image_features_ / image_features_norm_

            logits = compute_logits(args, feature_combination_module,
                                    image_features, image_features_,
                                    zeroshot_weights, group_images_shape[0])

            # measure accuracy
            if args.method == "equitune":
                acc1, acc5 = equitune_accuracy(logits, target, topk=(1, 5), group_name=group_name)
            elif args.method == "equizero":
                acc1, acc5 = equi0_accuracy(logits, target, topk=(1, 5), group_name=group_name)
            elif args.method == "attention":
                acc1, acc5 = accuracy(logits, target, topk=(1, 5))
            else:
                acc1, acc5 = equi0_accuracy(logits, target, topk=(1, 5), group_name=group_name) 
            top1 += acc1
            top5 += acc5
            n += images.size(0)

    top1 = (top1 / n) * 100
    top5 = (top5 / n) * 100

    info = [
        f"Dataset: {args.dataset_name}",
        f"Model: {args.model_name}",
        f"Method: {args.method}",
        f"Group: {args.group_name}",
        f"Data transformation: {args.data_transformations}",
        f"Top-1 accuracy: {top1:.2f}",
        f"Top-5 accuracy: {top5:.2f}"
    ]

    for message in info:
        logging.info(message)
        print(message)

    # Save the top-1 accuracy in a folder 
    if save_scores:
        folder = f"results/{args.dataset_name}/{args.model_name}/{args.method}/{args.data_transformations}"
        os.makedirs(folder, exist_ok=True)
        with open(f"{folder}/top1_accuracy.txt", "w") as f:
            f.write(f"{top1:.2f}")
        # save the top-5 accuracy as well
        with open(f"{folder}/top5_accuracy.txt", "w") as f:
            f.write(f"{top5:.2f}")
    current_time = time.time()
    time_elapsed = current_time - since
    print(f"time elapsed: {time_elapsed}")

    if val:
        return top1