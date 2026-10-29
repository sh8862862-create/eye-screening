"""Optimized FP32 hand pose with per-worker fallback to the original graph."""
from pathlib import Path
import time
import os
import cv2 as cv
from fast_handpose import FastMPHandPose
from mp_handpose import MPHandPose

class OptimizedHandPose(FastMPHandPose):
    def __init__(self,modelPath,**kwargs):
        super().__init__(modelPath,**kwargs)
        self.cpu_model=self.model
        self.backend_name='cpu_fallback'
        self.failure=''
        self.timings={}
        try:
            if os.environ.get('PYNQ_POSE_ENGINE','ncnn')=='ncnn':
                from ncnn_runtime import NcnnPoseNet
                self.model=NcnnPoseNet();self.backend_name='ncnn_fp32'
            else:
                from neon_runtime import register
                register()
                model=cv.dnn.readNet(str(Path(__file__).with_name('handpose_neon.onnx')))
                model.setPreferableBackend(self.backend_id);model.setPreferableTarget(self.target_id)
                self.model=model;self.backend_name='neon_fp32'
        except (OSError,ImportError,RuntimeError,cv.error,ValueError) as exc:
            self.failure=str(exc)

    def _preprocess(self,image,palm):
        if self.backend_name!='cpu_fallback':return super()._preprocess(image,palm)
        return MPHandPose._preprocess(self,image,palm)

    def infer(self,image,palm):
        original=palm.copy()
        try:
            start=time.perf_counter();args=self._preprocess(image,palm);pre=time.perf_counter()
            self.model.setInput(args[0])
            raw=self.model.forward(['Identity','Identity_1','Identity_2','Identity_3']);forward=time.perf_counter()
            hand=self._postprocess(raw,*args[1:]);end=time.perf_counter()
            self.timings=dict(pre_ms=round((pre-start)*1000,2),forward_ms=round((forward-pre)*1000,2),post_ms=round((end-forward)*1000,2))
            return hand
        except (cv.error,RuntimeError) as exc:
            if self.backend_name=='cpu_fallback':raise
            if hasattr(self.model,'close'):self.model.close()
            self.failure=str(exc);self.model=self.cpu_model;self.backend_name='cpu_fallback'
            return self.infer(image,original)
