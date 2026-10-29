"""Crop the rotated image before allocating/warping its unused outer region."""
import numpy as np
import cv2 as cv
from mp_handpose import MPHandPose

class FastMPHandPose(MPHandPose):
    def _preprocess(self,image,palm):
        bbox=palm[:4].reshape(2,2)
        bbox=bbox+self.PALM_BOX_PRE_SHIFT_VECTOR*(bbox[1]-bbox[0])
        center=np.sum(bbox,axis=0)/2
        half=(bbox[1]-bbox[0])*self.PALM_BOX_PRE_ENLARGE_FACTOR/2
        bbox=np.array([center-half,center+half]).astype(np.int32)
        bbox[:,0]=np.clip(bbox[:,0],0,image.shape[1]);bbox[:,1]=np.clip(bbox[:,1],0,image.shape[0])
        image=image[bbox[0,1]:bbox[1,1],bbox[0,0]:bbox[1,0]]
        if not image.size:raise ValueError('Empty hand crop')
        side=int(np.linalg.norm(image.shape[:2]))
        initial_pad=np.array([(side-image.shape[1])//2,(side-image.shape[0])//2])
        bias=bbox[0]-initial_pad
        bbox-=bias
        points=palm[4:18].reshape(7,2)-bias
        p1,p2=points[self.PALM_LANDMARKS_INDEX_OF_PALM_BASE],points[self.PALM_LANDMARKS_INDEX_OF_MIDDLE_FINGER_BASE]
        radians=np.pi/2-np.arctan2(-(p2[1]-p1[1]),p2[0]-p1[0])
        radians-=2*np.pi*np.floor((radians+np.pi)/(2*np.pi))
        angle=np.rad2deg(radians)
        matrix=cv.getRotationMatrix2D(np.sum(bbox,axis=0)/2,angle,1.)
        homogeneous=np.c_[points,np.ones(points.shape[0])]
        rotated=np.array([np.dot(homogeneous,matrix[0]),np.dot(homogeneous,matrix[1])])
        box=np.array([np.amin(rotated,axis=1),np.amax(rotated,axis=1)])
        box=box+self.PALM_BOX_SHIFT_VECTOR*(box[1]-box[0])
        center=np.sum(box,axis=0)/2
        half=(box[1]-box[0])*self.PALM_BOX_ENLARGE_FACTOR/2
        box=np.array([center-half,center+half]).astype(np.int32)
        box[:,0]=np.clip(box[:,0],0,side);box[:,1]=np.clip(box[:,1],0,side)
        x,y=box[0];width,height=box[1]-box[0]
        if width<=0 or height<=0:raise ValueError('Empty rotated hand crop')
        crop_matrix=matrix.copy();crop_matrix[:,2]+=matrix[:,:2]@initial_pad-box[0]
        crop=cv.warpAffine(image,crop_matrix,(int(width),int(height)))
        side=max(width,height);left=(side-width)//2;top=(side-height)//2
        crop=cv.copyMakeBorder(crop,int(top),int(side-height-top),int(left),int(side-width-left),cv.BORDER_CONSTANT,value=(0,0,0))
        blob=cv.cvtColor(cv.resize(crop,dsize=self.input_size,interpolation=cv.INTER_AREA),cv.COLOR_BGR2RGB).astype(np.float32)/255.
        return blob[np.newaxis],box,angle,matrix,bias
