#!/usr/bin/env python3
# Copyright (c) Facebook, Inc. and its affiliates.

"""
PointRend Training Script.

This script is a simplified version of the training script in detectron2/tools.
"""

import os, pdb, random
import torch

import detectron2.data.transforms as T
import detectron2.utils.comm as comm
from detectron2.checkpoint import DetectionCheckpointer
from detectron2.config import get_cfg
from detectron2.data import DatasetMapper, MetadataCatalog, build_detection_train_loader
from detectron2.engine import DefaultTrainer, default_argument_parser, default_setup, launch
from detectron2.evaluation import (
    COCOEvaluator,
    DatasetEvaluators,
    LVISEvaluator,
    SemSegEvaluator,
    verify_results,
)
from detectron2.projects.point_rend import ColorAugSSDTransform, add_pointrend_config

# register dataset
from detectron2.data import MetadataCatalog
from hos.data.datasets.epick import register_epick_instances


version = 'datasets/epick_visor_coco_active'

register_epick_instances("epick_visor_2022_train_active", {}, f"{version}/annotations/train.json", f"{version}/train")
register_epick_instances("epick_visor_2022_val_active", {}, f"{version}/annotations/val.json", f"{version}/val")
register_epick_instances("epick_visor_2022_test_active", {}, f"{version}/annotations/test.json", f"{version}/test")

MetadataCatalog.get("epick_visor_2022_train_active").thing_classes = ["hand", "object"]
MetadataCatalog.get("epick_visor_2022_val_active").thing_classes = ["hand", "object"]
MetadataCatalog.get("epick_visor_2022_test_active").thing_classes = ["hand", "object"]


def transfer_noun(noun):
    if ':' not in noun: 
        return noun
    List = noun.split(':')
    return ' '.join(List[1:]) + ' ' + List[0]


def get_category(csv_path='./data_prep/EPIC_100_noun_classes_v2.csv'):
    import csv
    key_dict = {}
    with open(csv_path, 'r') as csvfile:
        csvreader = csv.DictReader(csvfile)
        for row in csvreader:
            key_idx = int(row['id']) + 1
            key = transfer_noun( row['key'] )
            instances = [x.strip()[1:-1] for x in row['instances'][1:-1].split(',')]
            inst_ls = [transfer_noun(inst) for inst in instances]
            category = row['category']
            
            key_dict[key_idx] = {}
            key_dict[key_idx]['key'] = key
            key_dict[key_idx]['instances'] = inst_ls
            key_dict[key_idx]['category'] = category
            
    coco_categories = [ {'id':kind, 'name':kval['key']} for kind, kval in key_dict.items()]
    categories_ls = [ kval['key'] for kind, kval in key_dict.items()]
    return key_dict, coco_categories, categories_ls





def build_sem_seg_train_aug(cfg):
    augs = [
        T.ResizeShortestEdge(
            cfg.INPUT.MIN_SIZE_TRAIN, cfg.INPUT.MAX_SIZE_TRAIN, cfg.INPUT.MIN_SIZE_TRAIN_SAMPLING
        )
    ]
    if cfg.INPUT.CROP.ENABLED:
        augs.append(
            T.RandomCrop_CategoryAreaConstraint(
                cfg.INPUT.CROP.TYPE,
                cfg.INPUT.CROP.SIZE,
                cfg.INPUT.CROP.SINGLE_CATEGORY_MAX_AREA,
                cfg.MODEL.SEM_SEG_HEAD.IGNORE_VALUE,
            )
        )
    if cfg.INPUT.COLOR_AUG_SSD:
        augs.append(ColorAugSSDTransform(img_format=cfg.INPUT.FORMAT))
    # augs.append(T.RandomFlip())
    return augs


class Trainer(DefaultTrainer):
    """
    We use the "DefaultTrainer" which contains a number pre-defined logic for
    standard training workflow. They may not work for you, especially if you
    are working on a new research project. In that case you can use the cleaner
    "SimpleTrainer", or write your own training loop.
    """

    @classmethod
    def build_evaluator(cls, cfg, dataset_name, output_folder=None):
        """
        Create evaluator(s) for a given dataset.
        This uses the special metadata "evaluator_type" associated with each builtin dataset.
        For your own dataset, you can simply create an evaluator manually in your
        script and do not have to worry about the hacky if-else logic here.
        """
        if output_folder is None:
            output_folder = os.path.join(cfg.OUTPUT_DIR, "inference")
        evaluator_list = []
        evaluator_type = MetadataCatalog.get(dataset_name).evaluator_type
        
        if evaluator_type == "coco":
            return COCOEvaluator(dataset_name, output_dir=output_folder)
        if len(evaluator_list) == 0:
            raise NotImplementedError(
                "no Evaluator for the dataset {} with the type {}".format(
                    dataset_name, evaluator_type
                )
            )
        if len(evaluator_list) == 1:
            return evaluator_list[0]
        return DatasetEvaluators(evaluator_list)

    @classmethod
    def build_train_loader(cls, cfg):
        if "SemanticSegmentor" in cfg.MODEL.META_ARCHITECTURE:
            mapper = DatasetMapper(cfg, is_train=True, augmentations=build_sem_seg_train_aug(cfg))
        else:
            mapper = None
        return build_detection_train_loader(cfg, mapper=mapper)


def setup(args):
    """
    Create configs and perform basic setups.
    """
    cfg = get_cfg()
    add_pointrend_config(cfg)
    cfg.merge_from_file(args.config_file)
    cfg.merge_from_list(args.opts)
    
    if args.dataset == 'epick_hand':
        cfg.MODEL.ROI_HEADS.NUM_CLASSES = 1
        cfg.MODEL.POINT_HEAD.NUM_CLASSES = 1
    elif args.dataset == 'epick_all':
        cfg.MODEL.ROI_HEADS.NUM_CLASSES = 303
        cfg.MODEL.POINT_HEAD.NUM_CLASSES = 303
    else:
        cfg.MODEL.ROI_HEADS.NUM_CLASSES = 2
        cfg.MODEL.POINT_HEAD.NUM_CLASSES = 2
    
    # not flipping
    if args.dataset in ['epick_hand_leftright', 'epick_hos']:
        cfg.INPUT.RANDOM_FLIP = "none"
    cfg.freeze()
    default_setup(cfg, args)
    return cfg


def main(args):
    cfg = setup(args)
    print(f'here are the configs:\n {cfg}')
    # pdb.set_trace()
    if args.eval_only:
        model = Trainer.build_model(cfg)
        DetectionCheckpointer(model, save_dir=cfg.OUTPUT_DIR).resume_or_load(
            cfg.MODEL.WEIGHTS, resume=args.resume
        )
        res = Trainer.test(cfg, model)
        if comm.is_main_process():
            verify_results(cfg, res)
        return res

    trainer = Trainer(cfg)
    trainer.resume_or_load(resume=args.resume)
    return trainer.train()


if __name__ == "__main__":
    parser = default_argument_parser()
    parser.add_argument('--dataset', required=True, help='Dataset to train the model.')
    args = parser.parse_args()
    args.num_gpus = 2
    args.dist_url = f"tcp://127.0.0.1:8858"
    print("Command Line Args:", args)


    # run
    launch(
        main,
        args.num_gpus,
        num_machines=args.num_machines,
        machine_rank=args.machine_rank,
        dist_url=args.dist_url,
        args=(args,),
    )