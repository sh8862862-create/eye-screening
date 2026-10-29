"""Single-client PL model service. Never opens cameras or sends HID events."""
import grp
import argparse
import os
import signal
import socket
import struct
from pathlib import Path
import cv2 as cv
import numpy as np
import custom_layer
from pl_backend import PalmPL
from palm_rpc import SOCKET,INPUT_BYTES,INPUT_SHAPE,OUTPUT_SHAPES,OUTPUT_BYTES,receive_exact

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--socket',default=SOCKET)
    args=parser.parse_args()
    stopping=False
    def stop(*args):
        nonlocal stopping
        stopping=True  # Finish any in-flight DMA before releasing its buffers.
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    cv.setNumThreads(2)
    backend=PalmPL()
    path=Path(args.socket)
    try:
        custom_layer.BACKEND=backend
        if Path(__file__).with_name('libpalm_layer.so').exists():
            import native_layer
            native_layer.register()
        else:custom_layer.register()
        model=cv.dnn.readNet(str(Path(__file__).with_name('palm_custom.onnx')))
        path.parent.mkdir(mode=0o750,parents=True,exist_ok=True)
        if path.parent==Path(SOCKET).parent:
            os.chown(path.parent,-1,grp.getgrnam('xilinx').gr_gid)
        path.unlink(missing_ok=True)
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as listener:
            listener.bind(str(path))
            os.chown(path,-1,grp.getgrnam('xilinx').gr_gid);path.chmod(0o660)
            listener.listen(1);listener.settimeout(.5)
            notify=os.environ.get('NOTIFY_SOCKET')
            if notify:
                address='\0'+notify[1:] if notify.startswith('@') else notify
                with socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM) as status:
                    status.connect(address);status.sendall(b'READY=1')
            print('PALM_PL_READY',flush=True)
            while not stopping:
                try:connection,_=listener.accept()
                except socket.timeout:continue
                with connection:
                    connection.settimeout(2.)
                    try:
                        size=struct.unpack('!I',receive_exact(connection,4))[0]
                        if size!=INPUT_BYTES:raise ValueError('invalid request size')
                        blob=np.frombuffer(receive_exact(connection,size),dtype=np.float32).reshape(INPUT_SHAPE)
                        if not np.isfinite(blob).all():raise ValueError('nonfinite input')
                        if stopping:break
                        model.setInput(blob)
                        with backend.transaction():result=model.forward(['Identity','Identity_1'])
                        if tuple(x.shape for x in result)!=OUTPUT_SHAPES:
                            raise RuntimeError('model output shape differs from protocol')
                        data=b''.join(np.asarray(x,dtype=np.float32,order='C').tobytes() for x in result)
                        connection.sendall(struct.pack('!BI',0,OUTPUT_BYTES)+data)
                    except (OSError,ValueError,RuntimeError) as exc:
                        # A poisoned accelerator is fatal. Its exit cleanup retains
                        # the lease and buffers until idle, preventing owner reload.
                        if backend.poisoned:raise
                        message=str(exc).encode('utf-8')[:4096]
                        try:connection.sendall(struct.pack('!BI',1,len(message))+message)
                        except OSError:pass
    finally:
        path.unlink(missing_ok=True)
        backend.close()

if __name__=='__main__':main()
