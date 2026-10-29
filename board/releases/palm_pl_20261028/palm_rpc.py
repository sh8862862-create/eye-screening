"""Bounded binary transport between an unprivileged worker and the PL service."""
import socket
import struct
import numpy as np

SOCKET='/run/pynq-palm/palm.sock'
INPUT_SHAPE=(1,192,192,3)
OUTPUT_SHAPES=((1,2016,18),(1,2016,1))
INPUT_BYTES=int(np.prod(INPUT_SHAPE))*4
OUTPUT_BYTES=sum(int(np.prod(s))*4 for s in OUTPUT_SHAPES)

def receive_exact(connection,count):
    chunks=[]
    while count:
        chunk=connection.recv(min(count,65536))
        if not chunk:raise ConnectionError('PL service connection closed')
        chunks.append(chunk);count-=len(chunk)
    return b''.join(chunks)

def predict(blob,path=SOCKET,timeout=3.):
    blob=np.asarray(blob,dtype=np.float32,order='C')
    if blob.shape!=INPUT_SHAPE or not np.isfinite(blob).all():
        raise ValueError('invalid palm input')
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as connection:
        connection.settimeout(timeout);connection.connect(path)
        connection.sendall(struct.pack('!I',INPUT_BYTES)+blob.tobytes())
        status,size=struct.unpack('!BI',receive_exact(connection,5))
        if status:
            if size>4096:raise ValueError('invalid PL error length')
            raise RuntimeError(receive_exact(connection,size).decode('utf-8','replace'))
        if size!=OUTPUT_BYTES:raise ValueError('invalid PL output size')
        data=receive_exact(connection,size)
    result=[];offset=0
    for shape in OUTPUT_SHAPES:
        count=int(np.prod(shape))
        result.append(np.frombuffer(data,dtype=np.float32,count=count,offset=offset).reshape(shape))
        offset+=count*4
    if not all(np.isfinite(x).all() for x in result):raise ValueError('nonfinite PL output')
    return result

class PalmNetProxy:
    """Compatible with MPPalmDet's setInput/forward pair; CPU fallback latches."""
    def __init__(self,cpu_net,path=SOCKET):
        self.cpu_net=cpu_net;self.path=path;self.blob=None
        self.backend_name='pl';self.failure=''
    def setInput(self,blob):self.blob=blob
    def forward(self,names):
        if list(names)!=['Identity','Identity_1']:raise ValueError('unexpected palm outputs')
        if self.backend_name=='pl':
            try:return predict(self.blob,self.path)
            except (OSError,ValueError,RuntimeError) as exc:
                self.failure=str(exc);self.backend_name='cpu_fallback'
                print('Palm PL unavailable; using CPU for this worker:',self.failure,flush=True)
        self.cpu_net.setInput(self.blob)
        return self.cpu_net.forward(names)
