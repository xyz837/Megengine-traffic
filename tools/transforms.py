# -*- coding: utf-8 -*-
# MegEngine is Licensed under the Apache License, Version 2.0 (the "License")
#
# Copyright (c) 2014-2021 Megvii Inc. All rights reserved.
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT ARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
import collections.abc
import math
from typing import Sequence, Tuple
import copy
import inspect
import cv2
import numpy as np
from numpy import random
import json
from megengine.data.transform import Transform
from megengine.data.transform.vision import functional as F

__all__ = [
    "VisionTransform",
    "ToMode",
    "Compose",
    "TorchTransformCompose",
    "Pad",
    "Resize",
    "ShortestEdgeResize",
    "RandomResize",
    "RandomCrop",
    "RandomResizedCrop",
    "CenterCrop",
    "RandomHorizontalFlip",
    "RandomVerticalFlip",
    "Normalize",
    "GaussianNoise",
    "BrightnessTransform",
    "SaturationTransform",
    "ContrastTransform",
    "HueTransform",
    "ColorJitter",
    "Lighting",
    "Mixup",
    "Mosaic"
]

                



class VisionTransform(Transform):
    r"""
    Base class of all transforms used in computer vision.
    Calling logic: apply_batch() -> apply() -> _apply_image() and other _apply_*()
    method. If you want to implement a self-defined transform method for image,
    rewrite _apply_image method in subclass.

    :param order: input type order. Input is a tuple containing different structures,
        order is used to specify the order of structures. For example, if your input
        is (image, boxes) type, then the ``order`` should be ("image", "boxes").
        Current available strings and data type are describe below:

        * "image": input image, with shape of `(H, W, C)`.
        * "coords": coordinates, with shape of `(N, 2)`.
        * "boxes": bounding boxes, with shape of `(N, 4)`, "xyxy" format,
          the 1st "xy" represents top left point of a box,
          the 2nd "xy" represents right bottom point.
        * "mask": map used for segmentation, with shape of `(H, W, 1)`.
        * "keypoints": keypoints with shape of `(N, K, 3)`, N for number of instances,
          and K for number of keypoints in one instance. The first two dimensions
          of last axis is coordinate of keypoints and the the 3rd dimension is
          the label of keypoints.
        * "polygons": a sequence containing numpy arrays, its length is the number of instances.
          Each numpy array represents polygon coordinate of one instance.
        * "category": categories for some data type. For example, "image_category"
          means category of the input image and "boxes_category" means categories of
          bounding boxes.
        * "info": information for images such as image shapes and image path.

        You can also customize your data types only if you implement the corresponding
        _apply_*() methods, otherwise ``NotImplementedError`` will be raised.
    """

    def __init__(self, order=None):
        super().__init__()
        if order is None:
            order = ("image",)
        elif not isinstance(order, collections.abc.Sequence):
            raise ValueError(
                "order should be a sequence, but got order={}".format(order)
            )
        for k in order:
            if k in ("batch",):
                raise ValueError("{} is invalid data type".format(k))
            elif k.endswith("info"):
                # when the key is *category or info, we should do nothing
                # if the corresponding apply methods are not implemented.
                continue
            elif self._get_apply(k) is None:
                raise NotImplementedError("{} is unsupported data type".format(k))
        self.order = order

    def apply_batch(self, inputs: Sequence[Tuple]):
        r"""Apply transform on batch input data."""
        return tuple(self.apply(input) for input in inputs)

    def apply(self, input: Tuple):
        r"""Apply transform on single input data."""
        if not isinstance(input, tuple):
            input = (input,)

        output = []
        for i in range(min(len(input), len(self.order))):
            apply_func = self._get_apply(self.order[i])
            if apply_func is None:
                output.append(input[i])
            else:
                output.append(apply_func(input[i]))
        if len(input) > len(self.order):
            output.extend(input[len(self.order) :])

        if len(output) == 1:
            output = output[0]
        else:
            output = tuple(output)
        return output

    def _get_apply(self, key):
        return getattr(self, "_apply_{}".format(key), None)

    def _get_image(self, input: Tuple):
        if not isinstance(input, tuple):
            input = (input,)
        return input[self.order.index("image")]

    def _apply_image(self, image):
        raise NotImplementedError

    def _apply_coords(self, coords):
        raise NotImplementedError

    def _apply_boxes(self, boxes):
        idxs = np.array([(0, 1), (2, 1), (0, 3), (2, 3)]).flatten()
        coords = np.asarray(boxes).reshape(-1, 4)[:, idxs].reshape(-1, 2)
        coords = self._apply_coords(coords).reshape((-1, 4, 2))
        minxy = coords.min(axis=1)
        maxxy = coords.max(axis=1)
        trans_boxes = np.concatenate((minxy, maxxy), axis=1)
        return trans_boxes

    def _apply_mask(self, mask):
        raise NotImplementedError

    def _apply_keypoints(self, keypoints):
        coords, visibility = keypoints[..., :2], keypoints[..., 2:]
        trans_coords = [self._apply_coords(p) for p in coords]
        return np.concatenate((trans_coords, visibility), axis=-1)

    def _apply_polygons(self, polygons):
        return [[self._apply_coords(p) for p in instance] for instance in polygons]

class Mixup(VisionTransform):
    """Mixup images & bbox
    Args:
        prob (float): the probability of carrying out mixup process.
        lambd (float): the parameter for mixup.
        mixup (bool): mixup switch.
        json_path (string): the path to dataset json file.
    """

    def __init__(self, prob=0.5, lambd=0.5, mixup=False,
                 json_path='data/coco/annotations/pgtrainval2017.json',
                 img_path='data/coco/images/'):
        self.lambd = lambd
        self.prob = prob
        self.mixup = mixup
        self.json_path = json_path
        self.img_path = img_path
        with open(json_path, 'r') as json_file:
            all_labels = json.load(json_file)
        self.all_labels = all_labels

    def get_img2(self):
        # random get image2 for mixup
        idx2 = np.random.choice(np.arange(len(self.all_labels['images'])))
        img2_fn = self.all_labels['images'][idx2]['file_name']
        
        img2_id = self.all_labels['images'][idx2]['id']
        
        img2_path = self.img_path + img2_fn
        
        img2 = cv2.imread(img2_path)
        # get image2 label
        labels2 = []
        boxes2 = []
        for annt in self.all_labels['annotations']:
            if annt['image_id'] == img2_id:
                labels2.append(np.int64(annt['category_id']))
                boxes2.append([np.float32(annt['bbox'][0]),
                               np.float32(annt['bbox'][1]),
                               np.float32(annt['bbox'][0] + annt['bbox'][2] - 1),
                               np.float32(annt['bbox'][1] + annt['bbox'][3] - 1)])
        return img2, labels2, boxes2

    def apply(self, input):
        if self.mixup == True:
            if random.uniform(0, 1) > self.prob:
                # img1 = results['img']
                # labels1 = results['gt_labels']
                self._h, self._w, _ = self._get_image(input).shape
                self.category = input[self.order.index('boxes_category')]

                self.img2, self.labels2, self.boxes2 = self.get_img2()
                # if labels2 != []:
                #     break

                self.height = max(self._h, self.img2.shape[0])
                self.width = max(self._w, self.img2.shape[1])
                return super().apply(input)
            else:
                return input
    def _apply_image(self, input):
                if self.labels2 == []:
                    self.lambd = 0.9  # float(round(random.uniform(0.5,0.9),1))
                    # mix image
                    mixup_image = np.zeros([self.height, self.width, 3], dtype='float32')
                    mixup_image[:self._h, :self._w, :] = self._get_image(input).astype('float32') * self.lambd
                    mixup_image[:self.img2.shape[0], :self.img2.shape[1], :] += self.img2.astype('float32') * (1. - self.lambd)
                    mixup_image = mixup_image.astype('uint8')
                else:
                    # mix image
                    mixup_image = np.zeros([self.height, self.width, 3], dtype='float32')
                    mixup_image[:self._h, :self._w, :] = self._get_image(input).astype('float32') * self.lambd
                    mixup_image[:self.img2.shape[0], :self.img2.shape[1], :] += self.img2.astype('float32') * (1. - self.lambd)
                    mixup_image = mixup_image.astype('uint8')

                return mixup_image
                # if the image2 has not bboxes, the 'gt_labels' and 'gt_bboxes' need to be doubled
                # so at the end the half of loss weight can be added as 1 instead of 0.5
                # if boxes2 == []:
                #     results['gt_labels'] = np.hstack((labels1, labels1))
                #     results['gt_bboxes'] = np.vstack((list(results['gt_bboxes']), list(results['gt_bboxes'])))
                # else:
    def _apply_boxes(self, boxes):
       # mix labels
                # boxs['gt_labels'] = np.hstack((self._coord_info, np.array(self.labels2)))
        boxes = np.vstack((list(boxes), self.boxes2))
        return boxes
    def _apply_boxes_category(self,category):
        category = np.hstack((category, np.array(self.labels2)))
        
        return category

class Mosaic(VisionTransform):
    """Mixup images & bbox
    Args:
        prob (float): the probability of carrying out mixup process.
        lambd (float): the parameter for mixup.
        mixup (bool): mixup switch.
        json_path (string): the path to dataset json file.
    """

    def __init__(self, prob=0, flip: float = 0.5,mosaic=False,
                 json_path='data/coco/annotations/pgtrainval2017.json',
                 img_path='data/coco/images/'):
        self.prob = prob
        self.flip = flip
        self.mosaic = mosaic
        self.json_path = json_path
        self.img_path = img_path
        self.ratio_h =   random.uniform(0.4,0.6)
        self.ratio_w =   random.uniform(0.4,0.6)
        with open(json_path, 'r') as json_file:
            all_labels = json.load(json_file)
        self.all_labels = all_labels
        self.min_offset_x = 0.4
        self.min_offset_y = 0.4
        self.scale_low = 0.6
        self.scale_high = 0.8
        
    
    def cut(self,w,h):
        cut_x = np.random.randint(int(w*self.min_offset_x), int(w*(1 - self.min_offset_x)))
        cut_y = np.random.randint(int(h*self.min_offset_x), int(h*(1 - self.min_offset_x)))
        return cut_x,cut_y
    def place(self,w,h):
        place_x = [0,0,int(w*self.min_offset_x),int(w*self.min_offset_x)]
        place_y = [0,int(h*self.min_offset_y),int(w*self.min_offset_y),0]
        return place_x,place_y

    def get_img2(self):
        # random get image2 for mixup
        img = []
        labels = []
        boxes = []
        img_w = []
        img_h = []
        idx2 = np.random.choice(np.arange(len(self.all_labels['images'])),size = 3,replace = False)
        
#         print("图片名称",np.arange(len(self.all_labels['images'])))
        for i in idx2:
            img2_fn = self.all_labels['images'][i]['file_name']
            
            img2_id = self.all_labels['images'][i]['id']
            
            img2_path = self.img_path + img2_fn
            
            img2 = cv2.imread(img2_path)
            img_w.append(img2.shape[1])
            img_h.append(img2.shape[0])
            img.append(img2)
        # get image2 label
            labels2 = []
            boxes2 = []
            
            for annt in self.all_labels['annotations']:
                if annt['image_id'] == img2_id:
                    labels2.append(np.int64(annt['category_id']))
                    boxes2.append([np.float32(annt['bbox'][0]),
                                np.float32(annt['bbox'][1]),
                                np.float32(annt['bbox'][0] + annt['bbox'][2] - 1),
                                np.float32(annt['bbox'][1] + annt['bbox'][3] - 1)])
            labels.append(labels2)
            boxes.append(boxes2)
            
        return img, labels, boxes, img_w, img_h

    def apply(self, input):
        if self.mosaic == True:
            if random.uniform(0, 1) > self.prob:
                # img1 = results['img']
                # labels1 = results['gt_labels']
                self._flipped_1 = np.random.random() < self.flip
                self._flipped_2 = np.random.random() < self.flip
                self._flipped_3 = np.random.random() < self.flip
                self._flipped_4 = np.random.random() < self.flip
      
                self._h, self._w, _ = self._get_image(input).shape
                self.category = input[self.order.index('boxes_category')]
                self.img, self.labels, self.boxes,self.ex_img_w,self.ex_img_h = self.get_img2()
                # if labels2 != []:
                #     break
                
                self.w1,self.h1 = self.cut(self._w,self._h)
                self.h2 = self._h - self.h1
                self.w2 = self._w - self.w1
                # self.height = max(self._h, self.img2.shape[0])
                # self.width = max(self._w, self.img2.shape[1])
                return super().apply(input)
            else:
                return input
            
    def _apply_image(self, image):
                final_img = np.zeros((self._h,self._w,3),np.uint8)
#                 print('????????????')
#                 print(self._h,self._w)
#                 print('****************')
#                 print(self.h1,self.w1)
#                 print(self.h2,self.w2)
                if self._flipped_1:
                    image = F.flip(image, flipCode=1)
                if self._flipped_2:
                    self.img[0] = F.flip(self.img[0], flipCode=1)
                if self._flipped_3:
                    self.img[1] = F.flip(self.img[1], flipCode=1)
                if self._flipped_4:
                    self.img[2] = F.flip(self.img[2], flipCode=1)
                    
                self.nh = [0]*4
                self.nw = [0]*4
                scale = np.random.rand()*(0.5) + 0.5
#                 print("scale",scale)
                if self._w/self._h < 1:
                    self.nh[0] = int(scale*self._h)
                    self.nw[0] = int(self.nh[0]*(self._w/self._h))
                else:
                    self.nw[0] = int(scale*self._w)
                    self.nh[0] = int(self.nw[0]/(self._w/self._h))
                image = cv2.resize(image,(self.nw[0],self.nh[0]))
#                 print("画布长宽：",self._h,self._w)
#                 print("第0张：",image.shape)
                for i in range(len(self.ex_img_w)):
                    if self.ex_img_w[i]/self.ex_img_h[i] < 1:
                        self.nh[i+1] = int(scale*self.ex_img_h[i])
                        self.nw[i+1] = int(self.nh[i+1]*(self.ex_img_w[i]/self.ex_img_h[i]))
                    else:
                        self.nw[i+1] = int(scale*self.ex_img_w[i])
                        self.nh[i+1] = int(self.nw[i+1]/(self.ex_img_w[i]/self.ex_img_h[i]))
                    self.img[i] = cv2.resize(self.img[i],(self.nw[i+1],self.nh[i+1]))
#                     print("第",i+1,"张：",self.img[i].shape)
#                 print("mmm",self.nh[1],self.h2,self.w1)
                
                if self.nh[0] > self.h1 and self.nw[0] > self.w1:
                    final_img[:self.h1,:self.w1] = image[:self.h1,:self.w1]
                elif self.nh[0] > self.h1 and self.nw[0] < self.w1:
                    final_img[:self.h1,:self.nw[0]] = image[:self.h1,:]
                elif self.nh[0] < self.h1 and self.nw[0] > self.w1:
                    final_img[:self.nh[0],:self.w1] = image[:,:self.w1]
                else:
                    final_img[:self.nh[0],:self.nw[0]] = image
                    
                    
                
                if self.nh[1] > self.h2 and self.nw[1] > self.w1:
                    final_img[self.h1:,:self.w1] = self.img[0][(self.nh[1]-self.h2):,:self.w1]
                elif self.nh[1] > self.h2 and self.nw[1] < self.w1:
                    final_img[self.h1:,:self.nw[1]] = self.img[0][(self.nh[1]-self.h2):,:]
                elif self.nh[1] < self.h2 and self.nw[1] > self.w1:
                    final_img[self._h-self.nh[1]:,:self.w1] = self.img[0][:,:self.w1]
                else:
                    final_img[self._h-self.nh[1]:,:self.nw[1]] = self.img[0]
        
                    
                if self.nh[2] > self.h2 and self.nw[2] > self.w2:
                    final_img[self.h1:,self.w1:] = self.img[1][self.nh[2]-self.h2:,self.nw[2]-self.w2:]
                elif self.nh[2] > self.h2 and self.nw[2] < self.w2:
                    final_img[self.h1:,self._w-self.nw[2]:] = self.img[1][self.nh[2]-self.h2:,:]
                elif self.nh[2] < self.h2 and self.nw[2] > self.w2:
                    final_img[self._h-self.nh[2]:,self.w1:] = self.img[1][:,self.nw[2]-self.w2:]
                else:
                    final_img[self._h-self.nh[2]:,self._w-self.nw[2]:] = self.img[1]    
                    
                    
                if self.nh[3] > self.h1 and self.nw[3] > self.w2:
                    final_img[:self.h1,self.w1:] = self.img[2][:self.h1,self.nw[3]-self.w2:]
                elif self.nh[3] > self.h1 and self.nw[3] < self.w2:
                    final_img[:self.h1,self._w-self.nw[3]:] = self.img[2][:self.h1,:]
                elif self.nh[3] < self.h1 and self.nw[3] > self.w2:
                    final_img[:self.nh[3],self.w1:] = self.img[2][:,self.nw[3]-self.w2:]
                else:
                    final_img[:self.nh[3],self._w-self.nw[3]:] = self.img[2]
                dtype = final_img.dtype
                final_img = final_img.astype(np.uint8)
                hue = np.random.rand()*(0.2)-0.1
                sat = np.random.rand()*(0.5)+1 if np.random.rand()*(1)<0.5 else 1/(np.random.rand()*(0.5)+1)
                val = np.random.rand()*(0.5)+1 if np.random.rand()*(1)<0.5 else 1/(np.random.rand()*(0.5)+1)
                hsv_image = cv2.cvtColor(final_img, cv2.COLOR_BGR2HSV_FULL)
                h,s,v = cv2.split(hsv_image)
                h = h.astype(np.uint8)
                s = s.astype(np.float64)
                v = v.astype(np.float64)
                with np.errstate(over="ignore"):
                    h += np.uint8(hue * 255)
                    s *= sat
                    v *= val
                s = s.astype(np.uint8)
                v = v.astype(np.uint8)
                hsv_image = cv2.merge([h, s, v])
                final_img = cv2.cvtColor(hsv_image, cv2.COLOR_HSV2BGR_FULL).astype(dtype)      
#                 cv2.imwrite('first.jpg',final_img[:self.w1,:self.h1])
#                 cv2.imwrite('second.jpg',final_img[self.w1:,:self.h1])
#                 cv2.imwrite('third.jpg',final_img[self.w1:,self.h1:])
#                 cv2.imwrite('final.jpg',final_img)
                return final_img
                # if the image2 has not bboxes, the 'gt_labels' and 'gt_bboxes' need to be doubled
                # so at the end the half of loss weight can be added as 1 instead of 0.5
                # if boxes2 == []:
                #     results['gt_labels'] = np.hstack((labels1, labels1))
                #     results['gt_bboxes'] = np.vstack((list(results['gt_bboxes']), list(results['gt_bboxes'])))
                # else:                                self.ex_img_w,self.ex_img_h
    def _apply_boxes(self, boxes):
#         print("原始的boxes：",boxes)
#         print('boxes:',boxes)
#         print("test:",self.boxes[0])
        self.boxes[0] = np.array(self.boxes[0])
        self.boxes[1] = np.array(self.boxes[1])
        self.boxes[2] = np.array(self.boxes[2])
        if self._flipped_1:
            boxes[:, [0,2]] = self._w - boxes[:, [2,0]]
        if self._flipped_2:
            self.boxes[0][:, [0,2]] = self.ex_img_w[0] - self.boxes[0][:, [2,0]]
        if self._flipped_3:
            self.boxes[1][:, [0,2]] = self.ex_img_w[1] - self.boxes[1][:, [2,0]]
        if self._flipped_4:
            self.boxes[2][:, [0,2]] = self.ex_img_w[2] - self.boxes[2][:, [2,0]]
        
        boxes[:,[0,2]] = boxes[:,[0,2]]*self.nw[0]/self._w
        boxes[:,[1,3]] = boxes[:,[1,3]]*self.nh[0]/self._h
        boxes[:, 0:2][boxes[:, 0:2]<0] = 0
        boxes[:, 2][boxes[:, 2]>self.w1] = self.w1
        boxes[:, 3][boxes[:, 3]>self.h1] = self.h1
        
        boxes[:, 0][boxes[:, 0]>self.w1] = self.w1
        boxes[:, 1][boxes[:, 1]>self.h1] = self.h1
        boxes[:, 2:4][boxes[:, 2:4]<0] = 0
        
#         print("左上角图片的框：",boxes)


        self.boxes[0][:,[0,2]] = self.boxes[0][:,[0,2]]*self.nw[1]/self.ex_img_w[0]
        self.boxes[0][:,[1,3]] = self.boxes[0][:,[1,3]]*self.nh[1]/self.ex_img_h[0]+self._h-self.nh[1]
        self.boxes[0][:,0][self.boxes[0][:,0]<0] = 0
        self.boxes[0][:,1][self.boxes[0][:,1]<self.h1] = self.h1
        self.boxes[0][:,2][self.boxes[0][:,2]>self.w1] = self.w1
        self.boxes[0][:,3][self.boxes[0][:,3]>self._h] = self._h
        
        self.boxes[0][:,0][self.boxes[0][:,0]>self.w1] = self.w1
        self.boxes[0][:,1][self.boxes[0][:,1]>self._h] = self._h
        self.boxes[0][:,2][self.boxes[0][:,2]<0] = 0
        self.boxes[0][:,3][self.boxes[0][:,3]<self.h1] = self.h1
        
#         print("左下角图片的框：",self.boxes[0])



        self.boxes[1][:,[0,2]] = self.boxes[1][:,[0,2]]*self.nw[2]/self.ex_img_w[1]+self._w-self.nw[2]
        self.boxes[1][:,[1,3]] = self.boxes[1][:,[1,3]]*self.nh[2]/self.ex_img_h[1]+self._h-self.nh[2]
        
        self.boxes[1][:,0][self.boxes[1][:,0]<self.w1] = self.w1
        self.boxes[1][:,1][self.boxes[1][:,1]<self.h1] = self.h1
        self.boxes[1][:,2][self.boxes[1][:,2]>self._w] = self._w
        self.boxes[1][:,3][self.boxes[1][:,3]>self._h] = self._h
        
        self.boxes[1][:,0][self.boxes[1][:,0]>self._w] = self._w
        self.boxes[1][:,1][self.boxes[1][:,1]>self._h] = self._h
        self.boxes[1][:,2][self.boxes[1][:,2]<self.w1] = self.w1
        self.boxes[1][:,3][self.boxes[1][:,3]<self.h1] = self.h1
        
        self.boxes[2][:,[0,2]] = self.boxes[2][:,[0,2]]*self.nw[3]/self.ex_img_w[2]+self._w-self.nw[3]
        self.boxes[2][:,[1,3]] = self.boxes[2][:,[1,3]]*self.nh[3]/self.ex_img_h[2]
        self.boxes[2][:,0][self.boxes[2][:,0]<self.w1] = self.w1
        self.boxes[2][:,1][self.boxes[2][:,1]<0] = 0
        self.boxes[2][:,2][self.boxes[2][:,2]>self._w] = self._w
        self.boxes[2][:,3][self.boxes[2][:,3]>self.h1] = self.h1
        
        self.boxes[2][:,0][self.boxes[2][:,0]>self._w] = self._w
        self.boxes[2][:,1][self.boxes[2][:,1]>self.h1] = self.h1
        self.boxes[2][:,2][self.boxes[2][:,2]<self.w1] = self.w1
        self.boxes[2][:,3][self.boxes[2][:,3]<0] = 0
#         print("右上角图片的框：",self.boxes[2])
        boxes_final = np.zeros(4)
        boxes_final = np.vstack([boxes_final, boxes,self.boxes[0],self.boxes[1],self.boxes[2]])
        return boxes_final[1:]                          
        
#         for box in boxes:
#             boxes_final = np.vstack([boxes_final, [box[0]*self.ratio_w,box[1]*self.ratio_h,box[2]*self.ratio_w,box[3]*self.ratio_h]])
#         for box in self.boxes[0]:
#             boxes_final = np.vstack([boxes_final, [box[0]*self.w1/self.ex_img_w[0],box[1]*self.h2/self.ex_img_h[0]+self.h1,box[2]*self.w1/self.ex_img_w[0],box[3]*self.h2/self.ex_img_h[0]+self.h1]])
# #         print("test",self.w2,self.ex_img_w[0],box[2]*self.w2/self.ex_img_w[0]+self.w1)
#         for box in self.boxes[1]:
#             boxes_final = np.vstack([boxes_final, [box[0]*self.w2/self.ex_img_w[1]+self.w1,box[1]*self.h2/self.ex_img_h[1]+self.h1,box[2]*self.w2/self.ex_img_w[1]+self.w1,box[3]*self.h2/self.ex_img_h[1]+self.h1]])
#         for box in self.boxes[2]:
#             boxes_final = np.vstack([boxes_final, [box[0]*self.w2/self.ex_img_w[2]+self.w1,box[1]*self.h1/self.ex_img_h[2],box[2]*self.w2/self.ex_img_w[2]+self.w1,box[3]*self.h1/self.ex_img_h[2]]])
        
        
#         for box in boxes:
#             boxes_final = np.vstack([boxes_final, [box[0]*self.ratio_w,box[1]*self.ratio_h,box[2]*self.ratio_w,box[3]*self.ratio_h]])
#         for box in self.boxes[0]:
#             boxes_final = np.vstack([boxes_final, [box[0]*self.ex_img_w[0],box[1]*(1-self.ex_img_h[0])+self.h1,box[2]*self.ex_img_w[0],box[3]*(1-self.ex_img_h[0])+self.h1]])
#         for box in self.boxes[1]:
#             boxes_final = np.vstack([boxes_final, [box[0]*(1-self.ex_img_w[1])+self.w1,box[1]*(1-self.ex_img_h[1])+self.h1,box[2]*(1-self.ex_img_w[1])+self.w1,box[3]*(1-self.ex_img_h[1])+self.h1]])
#         for box in self.boxes[2]:
#             boxes_final = np.vstack([boxes_final, [box[0]*(1-self.ex_img_w[2])+self.w1,box[1]*self.ex_img_h[2],box[2]*(1-self.ex_img_w[2])+self.w1,box[3]*self.ex_img_h[2]]])
#         return boxes_final[1:]
    def _apply_boxes_category(self,category):
        category = np.hstack((category, np.array(self.labels[0]), np.array(self.labels[1]), np.array(self.labels[2])))
#         print("category:",category)
        
        return category    

class ToMode(VisionTransform):
    r"""
    Change input data to a target mode.
    For example, most transforms use HWC mode image,
    while the neural network might use CHW mode input tensor.

    :param mode: output mode of input. Default: "CHW"
    :param order: the same with :class:`VisionTransform`
    """

    def __init__(self, mode="CHW", *, order=None):
        super().__init__(order)
        assert mode in ["CHW"], "unsupported mode: {}".format(mode)
        self.mode = mode

    def _apply_image(self, image):
        if self.mode == "CHW":
            return np.ascontiguousarray(np.rollaxis(image, 2))
        return image

    def _apply_coords(self, coords):
        return coords

    def _apply_mask(self, mask):
        if self.mode == "CHW":
            return np.ascontiguousarray(np.rollaxis(mask, 2))
        return mask


class Compose(VisionTransform):
    r"""
    Composes several transforms together.

    :param transforms: list of :class:`VisionTransform` to compose.
    :param batch_compose: whether use shuffle_indices for batch data or not.
        If True, use original input sequence.
        Otherwise, the shuffle_indices will be used for transforms.
    :param shuffle_indices: indices used for random shuffle, start at 1.
        For example, if shuffle_indices is [(1, 3), (2, 4)], then the 1st and 3rd transform
        will be random shuffled, the 2nd and 4th transform will also be shuffled.
    :param order: the same with :class:`VisionTransform`

    Examples:

    .. testcode::

        from megengine.data.transform import RandomHorizontalFlip, RandomVerticalFlip, CenterCrop, ToMode, Compose

        transform_func = Compose([
            RandomHorizontalFlip(),
            RandomVerticalFlip(),
            CenterCrop(100),
            ToMode("CHW"),
            ],
            shuffle_indices=[(1, 2, 3)]
            )
    """

    def __init__(
        self, transforms=[], batch_compose=False, shuffle_indices=None, *, order=None
    ):
        super().__init__(order)
        self.transforms = transforms
        self._set_order()

        if batch_compose and shuffle_indices is not None:
            raise ValueError(
                "Do not support shuffle when apply transforms along the whole batch"
            )
        self.batch_compose = batch_compose

        if shuffle_indices is not None:
            shuffle_indices = [tuple(x - 1 for x in idx) for idx in shuffle_indices]
        self.shuffle_indices = shuffle_indices

    def _set_order(self):
        for t in self.transforms:
            t.order = self.order
            if isinstance(t, Compose):
                t._set_order()

    def apply_batch(self, inputs: Sequence[Tuple]):
        if self.batch_compose:
            for t in self.transforms:
                inputs = t.apply_batch(inputs)
            return inputs
        else:
            return super().apply_batch(inputs)

    def apply(self, input: Tuple):
        for t in self._shuffle():
            input = t.apply(input)
        return input

    def _shuffle(self):
        if self.shuffle_indices is not None:
            source_idx = list(range(len(self.transforms)))
            for idx in self.shuffle_indices:
                shuffled = np.random.permutation(idx).tolist()
                for src, dst in zip(idx, shuffled):
                    source_idx[src] = dst
            return [self.transforms[i] for i in source_idx]
        else:
            return self.transforms


class TorchTransformCompose(VisionTransform):
    r"""
    Compose class used for transforms in torchvision, only support PIL image,
    some transforms with tensor in torchvision are not supported,
    such as Normalize and ToTensor in torchvision.

    :param transforms: the same with ``Compose``.
    :param order: the same with :class:`VisionTransform`.
    """

    def __init__(self, transforms, *, order=None):
        super().__init__(order)
        self.transforms = transforms

    def _apply_image(self, image):
        from PIL import Image

        try:
            import accimage
        except ImportError:
            accimage = None

        if image.shape[0] == 3:  # CHW
            image = np.ascontiguousarray(image[[2, 1, 0]])
        elif image.shape[2] == 3:  # HWC
            image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        image = Image.fromarray(image.astype(np.uint8))

        for t in self.transforms:
            image = t(image)

        if isinstance(image, Image.Image) or (
            accimage is not None and isinstance(image, accimage.Image)
        ):
            image = np.array(image, dtype=np.uint8)
        if image.shape[0] == 3:  # CHW
            image = np.ascontiguousarray(image[[2, 1, 0]])
        elif image.shape[2] == 3:  # HWC
            image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
        return image


class Pad(VisionTransform):
    r"""
    Pad the input data.

    :param size: padding size of input image, it could be integer or sequence.
        If it is an integer, the input image will be padded in four directions.
        If it is a sequence containing two integers, the bottom and right side
        of image will be padded.
        If it is a sequence containing four integers, the top, bottom, left, right
        side of image will be padded with given size.
    :param value: padding value of image, could be a sequence of int or float.
        if it is float value, the dtype of image will be casted to float32 also.
    :param mask_value: padding value of segmentation map.
    :param order: the same with :class:`VisionTransform`.
    """

    def __init__(self, size=0, value=0, mask_value=0, *, order=None):
        super().__init__(order)
        if isinstance(size, int):
            size = (size, size, size, size)
        elif isinstance(size, collections.abc.Sequence) and len(size) == 2:
            size = (0, size[0], 0, size[1])
        elif not (isinstance(size, collections.abc.Sequence) and len(size) == 4):
            raise ValueError(
                "size should be a list/tuple which contains "
                "(top, down, left, right) four pad sizes."
            )
        self.size = size
        self.value = value
        if not isinstance(mask_value, int):
            raise ValueError(
                "mask_value should be a positive integer, "
                "but got mask_value={}".format(mask_value)
            )
        self.mask_value = mask_value

    def _apply_image(self, image):
        return F.pad(image, self.size, self.value)

    def _apply_coords(self, coords):
        coords[:, 0] += self.size[2]
        coords[:, 1] += self.size[0]
        return coords

    def _apply_mask(self, mask):
        return F.pad(mask, self.size, self.mask_value)


class Resize(VisionTransform):
    r"""
    Resize the input data.

    :param output_size: target size of image, with (height, width) shape.
    :param interpolation: interpolation method. All methods are listed below:

        * cv2.INTER_NEAREST – a nearest-neighbor interpolation.
        * cv2.INTER_LINEAR – a bilinear interpolation (used by default).
        * cv2.INTER_AREA – resampling using pixel area relation.
        * cv2.INTER_CUBIC – a bicubic interpolation over 4×4 pixel neighborhood.
        * cv2.INTER_LANCZOS4 – a Lanczos interpolation over 8×8 pixel neighborhood.
    :param order: the same with :class:`VisionTransform`.
    """

    def __init__(self, output_size, interpolation=cv2.INTER_LINEAR, *, order=None):
        super().__init__(order)
        self.output_size = output_size
        self.interpolation = interpolation

    def apply(self, input: Tuple):
        self._shape_info = self._get_shape(self._get_image(input))
        return super().apply(input)

    def _apply_image(self, image):
        h, w, th, tw = self._shape_info
        if h == th and w == tw:
            return image
        return F.resize(image, (th, tw), self.interpolation)

    def _apply_coords(self, coords):
        h, w, th, tw = self._shape_info
        if h == th and w == tw:
            return coords
        coords[:, 0] = coords[:, 0] * (tw / w)
        coords[:, 1] = coords[:, 1] * (th / h)
        return coords

    def _apply_mask(self, mask):
        h, w, th, tw = self._shape_info
        if h == th and w == tw:
            return mask
        return F.resize(mask, (th, tw), cv2.INTER_NEAREST)

    def _get_shape(self, image):
        h, w, _ = image.shape
        if isinstance(self.output_size, int):
            if min(h, w) == self.output_size:
                return h, w, h, w
            if h < w:
                th = self.output_size
                tw = int(self.output_size * w / h)
            else:
                tw = self.output_size
                th = int(self.output_size * h / w)
            return h, w, th, tw
        else:
            return (h, w, *self.output_size)


class ShortestEdgeResize(VisionTransform):
    r"""
    Resize the input data with specified shortset edge.
    """

    def __init__(
        self,
        min_size,
        max_size,
        sample_style="range",
        interpolation=cv2.INTER_LINEAR,
        *,
        order=None
    ):
        super().__init__(order)
        if sample_style not in ("range", "choice"):
            raise NotImplementedError(
                "{} is unsupported sample style".format(sample_style)
            )
        self.sample_style = sample_style
        if isinstance(min_size, int):
            min_size = (min_size, min_size)
        self.min_size = min_size
        self.max_size = max_size
        self.interpolation = interpolation

    def apply(self, input: Tuple):
        self._shape_info = self._get_shape(self._get_image(input))
        return super().apply(input)

    def _apply_image(self, image):
        h, w, th, tw = self._shape_info
        if h == th and w == tw:
            return image
        return F.resize(image, (th, tw), self.interpolation)

    def _apply_coords(self, coords):
        h, w, th, tw = self._shape_info
        if h == th and w == tw:
            return coords
        coords[:, 0] = coords[:, 0] * (tw / w)
        coords[:, 1] = coords[:, 1] * (th / h)
        return coords

    def _apply_mask(self, mask):
        h, w, th, tw = self._shape_info
        if h == th and w == tw:
            return mask
        return F.resize(mask, (th, tw), cv2.INTER_NEAREST)

    def _get_shape(self, image):
        h, w, _ = image.shape
        if self.sample_style == "range":
            size = np.random.randint(self.min_size[0], self.min_size[1] + 1)
        else:
            size = np.random.choice(self.min_size)

        scale = size / min(h, w)
        if h < w:
            th, tw = size, scale * w
        else:
            th, tw = scale * h, size
        if max(th, tw) > self.max_size:
            scale = self.max_size / max(th, tw)
            th = th * scale
            tw = tw * scale
        th = int(round(th))
        tw = int(round(tw))
        return h, w, th, tw


class RandomResize(VisionTransform):
    r"""
    Resize the input data randomly.

    :param scale_range: range of scaling.
    :param order: the same with :class:`VisionTransform`.
    """

    def __init__(self, scale_range, interpolation=cv2.INTER_LINEAR, *, order=None):
        super().__init__(order)
        self.scale_range = scale_range
        self.interpolation = interpolation

    def apply(self, input: Tuple):
        self._shape_info = self._get_shape(self._get_image(input))
        return super().apply(input)

    def _apply_image(self, image):
        h, w, th, tw = self._shape_info
        if h == th and w == tw:
            return image
        return F.resize(image, (th, tw), self.interpolation)

    def _apply_coords(self, coords):
        h, w, th, tw = self._shape_info
        if h == th and w == tw:
            return coords
        coords[:, 0] = coords[:, 0] * (tw / w)
        coords[:, 1] = coords[:, 1] * (th / h)
        return coords

    def _apply_mask(self, mask):
        h, w, th, tw = self._shape_info
        if h == th and w == tw:
            return mask
        return F.resize(mask, (th, tw), cv2.INTER_NEAREST)

    def _get_shape(self, image):
        h, w, _ = image.shape
        scale = np.random.uniform(*self.scale_range)
        th = int(round(h * scale))
        tw = int(round(w * scale))
        return h, w, th, tw


class RandomCrop(VisionTransform):
    r"""
    Crop the input data randomly. Before applying the crop transform,
    pad the image first. If target size is still bigger than the size of
    padded image, pad the image size to target size.

    :param output_size: target size of output image, with (height, width) shape.
    :param padding_size: the same with `size` in ``Pad``.
    :param padding_value: the same with `value` in ``Pad``.
    :param order: the same with :class:`VisionTransform`.
    """

    def __init__(
        self,
        output_size,
        padding_size=0,
        padding_value=[0, 0, 0],
        padding_maskvalue=0,
        *,
        order=None
    ):
        super().__init__(order)
        if isinstance(output_size, int):
            self.output_size = (output_size, output_size)
        else:
            self.output_size = output_size
        self.pad = Pad(padding_size, padding_value, order=self.order)
        self.padding_value = padding_value
        self.padding_maskvalue = padding_maskvalue

    def apply(self, input):
        input = self.pad.apply(input)
        self._h, self._w, _ = self._get_image(input).shape
        self._th, self._tw = self.output_size
        self._x = np.random.randint(0, max(0, self._w - self._tw) + 1)
        self._y = np.random.randint(0, max(0, self._h - self._th) + 1)
        return super().apply(input)

    def _apply_image(self, image):
        if self._th > self._h:
            image = F.pad(image, (self._th - self._h, 0), self.padding_value)
        if self._tw > self._w:
            image = F.pad(image, (0, self._tw - self._w), self.padding_value)
        return image[self._y : self._y + self._th, self._x : self._x + self._tw]

    def _apply_coords(self, coords):
        coords[:, 0] -= self._x
        coords[:, 1] -= self._y
        return coords

    def _apply_mask(self, mask):
        if self._th > self._h:
            mask = F.pad(mask, (self._th - self._h, 0), self.padding_maskvalue)
        if self._tw > self._w:
            mask = F.pad(mask, (0, self._tw - self._w), self.padding_maskvalue)
        return mask[self._y : self._y + self._th, self._x : self._x + self._tw]


class RandomResizedCrop(VisionTransform):
    r"""
    Crop the input data to random size and aspect ratio.
    A crop of random size (default: of 0.08 to 1.0) of the original size and a random
    aspect ratio (default: of 3/4 to 1.33) of the original aspect ratio is made.
    After applying crop transfrom, the input data will be resized to given size.

    :param output_size: target size of output image, with (height, width) shape.
    :param scale_range: range of size of the origin size cropped. Default: (0.08, 1.0)
    :param ratio_range: range of aspect ratio of the origin aspect ratio cropped. Default: (0.75, 1.33)
    :param order: the same with :class:`VisionTransform`.
    """

    def __init__(
        self,
        output_size,
        scale_range=(0.08, 1.0),
        ratio_range=(3.0 / 4, 4.0 / 3),
        interpolation=cv2.INTER_LINEAR,
        *,
        order=None
    ):
        super().__init__(order)
        if isinstance(output_size, int):
            self.output_size = (output_size, output_size)
        else:
            self.output_size = output_size
        assert (
            scale_range[0] <= scale_range[1]
        ), "scale_range should be of kind (min, max)"
        assert (
            ratio_range[0] <= ratio_range[1]
        ), "ratio_range should be of kind (min, max)"
        self.scale_range = scale_range
        self.ratio_range = ratio_range
        self.interpolation = interpolation

    def apply(self, input: Tuple):
        self._coord_info = self._get_coord(self._get_image(input))
        return super().apply(input)

    def _apply_image(self, image):
        x, y, w, h = self._coord_info
        cropped_img = image[y : y + h, x : x + w]
        return F.resize(cropped_img, self.output_size, self.interpolation)

    def _apply_coords(self, coords):
        x, y, w, h = self._coord_info
        coords[:, 0] = (coords[:, 0] - x) * self.output_size[1] / w
        coords[:, 1] = (coords[:, 1] - y) * self.output_size[0] / h
        return coords

    def _apply_mask(self, mask):
        x, y, w, h = self._coord_info
        cropped_mask = mask[y : y + h, x : x + w]
        return F.resize(cropped_mask, self.output_size, cv2.INTER_NEAREST)

    def _get_coord(self, image, attempts=10):
        height, width, _ = image.shape
        area = height * width

        for _ in range(attempts):
            target_area = np.random.uniform(*self.scale_range) * area
            log_ratio = tuple(math.log(x) for x in self.ratio_range)
            aspect_ratio = math.exp(np.random.uniform(*log_ratio))

            w = int(round(math.sqrt(target_area * aspect_ratio)))
            h = int(round(math.sqrt(target_area / aspect_ratio)))

            if 0 < w <= width and 0 < h <= height:
                x = np.random.randint(0, width - w + 1)
                y = np.random.randint(0, height - h + 1)
                return x, y, w, h

        # Fallback to central crop
        in_ratio = float(width) / float(height)
        if in_ratio < min(self.ratio_range):
            w = width
            h = int(round(w / min(self.ratio_range)))
        elif in_ratio > max(self.ratio_range):
            h = height
            w = int(round(h * max(self.ratio_range)))
        else:  # whole image
            w = width
            h = height
        x = (width - w) // 2
        y = (height - h) // 2
        return x, y, w, h


class CenterCrop(VisionTransform):
    r"""
    Crops the given the input data at the center.

    :param output_size: target size of output image, with (height, width) shape.
    :param order: the same with :class:`VisionTransform`.
    """

    def __init__(self, output_size, *, order=None):
        super().__init__(order)
        if isinstance(output_size, int):
            self.output_size = (output_size, output_size)
        else:
            self.output_size = output_size

    def apply(self, input: Tuple):
        self._coord_info = self._get_coord(self._get_image(input))
        return super().apply(input)

    def _apply_image(self, image):
        x, y = self._coord_info
        th, tw = self.output_size
        return image[y : y + th, x : x + tw]

    def _apply_coords(self, coords):
        x, y = self._coord_info
        coords[:, 0] -= x
        coords[:, 1] -= y
        return coords

    def _apply_mask(self, mask):
        x, y = self._coord_info
        th, tw = self.output_size
        return mask[y : y + th, x : x + tw]

    def _get_coord(self, image):
        th, tw = self.output_size
        h, w, _ = image.shape
        assert th <= h and tw <= w, "output size is bigger than image size"
        x = int(round((w - tw) / 2.0))
        y = int(round((h - th) / 2.0))
        return x, y


class RandomHorizontalFlip(VisionTransform):
    r"""
    Horizontally flip the input data randomly with a given probability.

    :param p: probability of the input data being flipped. Default: 0.5
    :param order: the same with :class:`VisionTransform`.
    """

    def __init__(self, prob: float = 0.5, *, order=None):
        super().__init__(order)
        self.prob = prob

    def apply(self, input: Tuple):
        self._flipped = np.random.random() < self.prob
        self._w = self._get_image(input).shape[1]
        return super().apply(input)

    def _apply_image(self, image):
        if self._flipped:
            return F.flip(image, flipCode=1)
        return image

    def _apply_coords(self, coords):
        if self._flipped:
            coords[:, 0] = self._w - coords[:, 0]
        return coords

    def _apply_mask(self, mask):
        if self._flipped:
            return F.flip(mask, flipCode=1)
        return mask


class RandomVerticalFlip(VisionTransform):
    r"""
    Vertically flip the input data randomly with a given probability.

    :param p: probability of the input data being flipped. Default: 0.5
    :param order: the same with :class:`VisionTransform`.
    """

    def __init__(self, prob: float = 0.5, *, order=None):
        super().__init__(order)
        self.prob = prob

    def apply(self, input: Tuple):
        self._flipped = np.random.random() < self.prob
        self._h = self._get_image(input).shape[0]
        return super().apply(input)

    def _apply_image(self, image):
        if self._flipped:
            return F.flip(image, flipCode=0)
        return image

    def _apply_coords(self, coords):
        if self._flipped:
            coords[:, 1] = self._h - coords[:, 1]
        return coords

    def _apply_mask(self, mask):
        if self._flipped:
            return F.flip(mask, flipCode=0)
        return mask


class Normalize(VisionTransform):
    r"""
    Normalize the input data with mean and standard deviation.
    Given mean: ``(M1,...,Mn)`` and std: ``(S1,..,Sn)`` for ``n`` channels,
    this transform will normalize each channel of the input data.
    ``output[channel] = (input[channel] - mean[channel]) / std[channel]``

    :param mean: sequence of means for each channel.
    :param std: sequence of standard deviations for each channel.
    :param order: the same with :class:`VisionTransform`.
    """

    def __init__(self, mean=0.0, std=1.0, *, order=None):
        super().__init__(order)
        self.mean = np.array(mean, dtype=np.float32)
        self.std = np.array(std, dtype=np.float32)

    def _apply_image(self, image):
        return (image - self.mean) / self.std

    def _apply_coords(self, coords):
        return coords

    def _apply_mask(self, mask):
        return mask


class GaussianNoise(VisionTransform):
    r"""
    Add random gaussian noise to the input data.
    Gaussian noise is generated with given mean and std.

    :param mean: Gaussian mean used to generate noise.
    :param std: Gaussian standard deviation used to generate noise.
    :param order: the same with :class:`VisionTransform`
    """

    def __init__(self, mean=0.0, std=1.0, *, order=None):
        super().__init__(order)
        self.mean = np.array(mean, dtype=np.float32)
        self.std = np.array(std, dtype=np.float32)

    def _apply_image(self, image):
        dtype = image.dtype
        noise = np.random.normal(self.mean, self.std, image.shape) * 255
        image = image + noise.astype(np.float32)
        return np.clip(image, 0, 255).astype(dtype)

    def _apply_coords(self, coords):
        return coords

    def _apply_mask(self, mask):
        return mask


class BrightnessTransform(VisionTransform):
    r"""
    Adjust brightness of the input data.

    :param value: how much to adjust the brightness. Can be any
        non negative number. 0 gives the original image.
    :param order: the same with :class:`VisionTransform`.
    """

    def __init__(self, value, *, order=None):
        super().__init__(order)
        if value < 0:
            raise ValueError("brightness value should be non-negative")
        self.value = value

    def _apply_image(self, image):
        if self.value == 0:
            return image

        dtype = image.dtype
        image = image.astype(np.float32)
        alpha = np.random.uniform(max(0, 1 - self.value), 1 + self.value)
        image = image * alpha
        return image.clip(0, 255).astype(dtype)

    def _apply_coords(self, coords):
        return coords

    def _apply_mask(self, mask):
        return mask


class ContrastTransform(VisionTransform):
    r"""
    Adjust contrast of the input data.

    :param value: how much to adjust the contrast. Can be any
        non negative number. 0 gives the original image.
    :param order: the same with :class:`VisionTransform`.
    """

    def __init__(self, value, *, order=None):
        super().__init__(order)
        if value < 0:
            raise ValueError("contrast value should be non-negative")
        self.value = value

    def _apply_image(self, image):
        if self.value == 0:
            return image

        dtype = image.dtype
        image = image.astype(np.float32)
        alpha = np.random.uniform(max(0, 1 - self.value), 1 + self.value)
        image = image * alpha + F.to_gray(image).mean() * (1 - alpha)
        return image.clip(0, 255).astype(dtype)

    def _apply_coords(self, coords):
        return coords

    def _apply_mask(self, mask):
        return mask


class SaturationTransform(VisionTransform):
    r"""
    Adjust saturation of the input data.

    :param value: how much to adjust the saturation. Can be any
        non negative number. 0 gives the original image.
    :param order: the same with :class:`VisionTransform`.
    """

    def __init__(self, value, *, order=None):
        super().__init__(order)
        if value < 0:
            raise ValueError("saturation value should be non-negative")
        self.value = value

    def _apply_image(self, image):
        if self.value == 0:
            return image

        dtype = image.dtype
        image = image.astype(np.float32)
        alpha = np.random.uniform(max(0, 1 - self.value), 1 + self.value)
        image = image * alpha + F.to_gray(image) * (1 - alpha)
        return image.clip(0, 255).astype(dtype)

    def _apply_coords(self, coords):
        return coords

    def _apply_mask(self, mask):
        return mask


class HueTransform(VisionTransform):
    r"""
    Adjust hue of the input data.

    :param value: how much to adjust the hue. Can be any number
        between 0 and 0.5, 0 gives the original image.
    :param order: the same with :class:`VisionTransform`.
    """

    def __init__(self, value, *, order=None):
        super().__init__(order)
        if value < 0 or value > 0.5:
            raise ValueError("hue value should be in [0.0, 0.5]")
        self.value = value

    def _apply_image(self, image):
        if self.value == 0:
            return image

        dtype = image.dtype
        image = image.astype(np.uint8)
        hsv_image = cv2.cvtColor(image, cv2.COLOR_BGR2HSV_FULL)
        h, s, v = cv2.split(hsv_image)

        alpha = np.random.uniform(-self.value, self.value)
        h = h.astype(np.uint8)
        # uint8 addition take cares of rotation across boundaries
        with np.errstate(over="ignore"):
            h += np.uint8(alpha * 255)
        hsv_image = cv2.merge([h, s, v])
        return cv2.cvtColor(hsv_image, cv2.COLOR_HSV2BGR_FULL).astype(dtype)

    def _apply_coords(self, coords):
        return coords

    def _apply_mask(self, mask):
        return mask


class ColorJitter(VisionTransform):
    r"""
    Randomly change the brightness, contrast, saturation and hue of an image.

    :param brightness: how much to jitter brightness.
        Chosen uniformly from [max(0, 1 - brightness), 1 + brightness]
        or the given [min, max]. Should be non negative numbers.
    :param contrast: how much to jitter contrast.
        Chosen uniformly from [max(0, 1 - contrast), 1 + contrast]
        or the given [min, max]. Should be non negative numbers.
    :param saturation: how much to jitter saturation.
        Chosen uniformly from [max(0, 1 - saturation), 1 + saturation]
        or the given [min, max]. Should be non negative numbers.
    :param hue: how much to jitter hue.
        Chosen uniformly from [-hue, hue] or the given [min, max].
        Should have 0<= hue <= 0.5 or -0.5 <= min <= max <= 0.5.
    :param order: the same with :class:`VisionTransform`.
    """

    def __init__(self, brightness=0, contrast=0, saturation=0, hue=0, *, order=None):
        super().__init__(order)
        transforms = []
        if brightness != 0:
            transforms.append(BrightnessTransform(brightness))
        if contrast != 0:
            transforms.append(ContrastTransform(contrast))
        if saturation != 0:
            transforms.append(SaturationTransform(saturation))
        if hue != 0:
            transforms.append(HueTransform(hue))
        self.transforms = Compose(
            transforms,
            shuffle_indices=[tuple(range(1, len(transforms) + 1))],
            order=order,
        )

    def apply(self, input):
        return self.transforms.apply(input)


class Lighting(VisionTransform):
    r"""
    Apply AlexNet-Style "lighting" augmentation to input data.

    Input images are assumed to have 'RGB' channel order.

    The degree of color jittering is randomly sampled via a normal distribution,
    with standard deviation given by the scale parameter.
    """

    def __init__(self, scale, *, order=None):
        super().__init__(order)
        if scale < 0:
            raise ValueError("lighting scale should be non-negative")
        self.scale = scale
        self.eigvec = np.array(
            [
                [-0.5836, -0.6948, 0.4203],
                [-0.5808, -0.0045, -0.8140],
                [-0.5675, 0.7192, 0.4009],
            ]
        )  # reverse the first dimension for BGR
        self.eigval = np.array([0.2175, 0.0188, 0.0045])

    def _apply_image(self, image):
        if self.scale == 0:
            return image

        dtype = image.dtype
        image = image.astype(np.float32)
        alpha = np.random.normal(scale=self.scale * 255, size=3)
        image = image + self.eigvec.dot(alpha * self.eigval)
        return image.clip(0, 255).astype(dtype)

    def _apply_coords(self, coords):
        return coords

    def _apply_mask(self, mask):
        return mask
