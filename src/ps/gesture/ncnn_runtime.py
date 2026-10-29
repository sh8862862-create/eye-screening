import ctypes
from pathlib import Path
import numpy as np

class NcnnPoseNet:
    def __init__(self,threads=2):
        b=Path(__file__).resolve().parent
        self.library=ctypes.CDLL(str(b/'libgesture_ncnn.so'))
        self.library.create_pose_net.argtypes=[ctypes.c_char_p,ctypes.c_char_p,ctypes.c_int]
        self.library.create_pose_net.restype=ctypes.c_void_p
        self.library.destroy_pose_net.argtypes=[ctypes.c_void_p]
        self.library.forward_pose_net.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_float),ctypes.POINTER(ctypes.c_float)]
        self.library.forward_pose_net.restype=ctypes.c_int
        self.handle=self.library.create_pose_net(str(b/'handpose.ncnn.param').encode(),str(b/'handpose.ncnn.bin').encode(),threads)
        if not self.handle:raise RuntimeError('Cannot load ncnn hand-pose graph')
        self.input=None
    def setInput(self,blob):
        if blob.shape!=(1,224,224,3) or not np.isfinite(blob).all():raise ValueError('Invalid hand-pose input')
        self.input=np.ascontiguousarray(blob,dtype=np.float32)
    def forward(self,names):
        if self.input is None:raise RuntimeError('Input missing')
        output=np.empty(128,np.float32);pointer=ctypes.POINTER(ctypes.c_float)
        code=self.library.forward_pose_net(self.handle,self.input.ctypes.data_as(pointer),output.ctypes.data_as(pointer))
        if code or not np.isfinite(output).all():raise RuntimeError('ncnn forward failed: '+str(code))
        values={'Identity':output[:63].reshape(1,63),'Identity_1':output[63:64].reshape(1,1),
          'Identity_2':output[64:65].reshape(1,1),'Identity_3':output[65:].reshape(1,63)}
        return [values[name] for name in names]
    def close(self):
        if getattr(self,'handle',None):self.library.destroy_pose_net(self.handle);self.handle=None
    def __del__(self):self.close()
