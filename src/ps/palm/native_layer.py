"""Register the compiled OpenCV layer with synchronous, zero-copy Mat views."""
import ctypes
from pathlib import Path
import numpy as np
import custom_layer

CALLBACK=ctypes.CFUNCTYPE(ctypes.c_int,ctypes.c_int,
    ctypes.POINTER(ctypes.c_float),ctypes.POINTER(ctypes.c_float),
    *([ctypes.c_int]*7))
_callback=None
_library=None
last_error=''

def register():
    global _callback,_library
    weights={k:custom_layer.WEIGHTS[k] for k in custom_layer.WEIGHTS.files}
    def forward(key,input_pointer,output_pointer,channels,height,width,oh,ow,stride,pad):
        global last_error
        try:
            x=np.ctypeslib.as_array(input_pointer,shape=(1,channels,height,width))
            destination=np.ctypeslib.as_array(output_pointer,shape=(1,channels,oh,ow))
            custom_layer.BACKEND.forward(x,weights['w'+str(key)],
                weights['b'+str(key)],stride,pad,oh,ow,destination=destination)
            return 0
        except BaseException as exc:
            last_error=str(exc)
            print('Native PL layer failed:',last_error,flush=True)
            return -1
    _callback=CALLBACK(forward)
    _library=ctypes.CDLL(str(Path(__file__).with_name('libpalm_layer.so')))
    _library.register_palm_layer.argtypes=[CALLBACK]
    _library.register_palm_layer.restype=None
    _library.register_palm_layer(_callback)
