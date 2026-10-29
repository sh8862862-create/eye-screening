"""Candidate OpenCV dispatch boundary. CPU reference unless a backend is injected."""
from pathlib import Path
import numpy as np
import cv2 as cv

WEIGHTS=np.load(Path(__file__).with_name('weights.npz'))
BACKEND=None

class Depthwise:
    def __init__(self,params,blobs):
        self.key=str(int(params['layer_id']))
        self.stride=int(params['stride']); self.pad=int(params['pad'])
        self.h=int(params['out_h']);self.w=int(params['out_w'])
        self.weights=WEIGHTS['w'+self.key];self.bias=WEIGHTS['b'+self.key]
    def getMemoryShapes(self,inputs):
        return [[1,int(self.weights.shape[0]),self.h,self.w]]
    def forward(self,inputs):
        x=np.ascontiguousarray(inputs[0],dtype=np.float32)
        if BACKEND is not None:
            return [BACKEND.forward(x,self.weights,self.bias,self.stride,self.pad,self.h,self.w)]
        padded=np.pad(x,((0,0),(0,0),(self.pad,4),(self.pad,4)))
        out=np.broadcast_to(self.bias.reshape(1,-1,1,1),(1,x.shape[1],self.h,self.w)).copy()
        for ky in range(5):
            for kx in range(5):
                out += padded[:,:,ky:ky+self.h*self.stride:self.stride,kx:kx+self.w*self.stride:self.stride]*self.weights[:,0,ky,kx].reshape(1,-1,1,1)
        return [out]

def register():
    cv.dnn_registerLayer('PynqDepthwise',Depthwise)
