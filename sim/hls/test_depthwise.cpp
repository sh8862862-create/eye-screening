#include <cmath>
#include <cstdio>
#include <vector>
extern "C" int palm_depthwise(const float*,const float*,const float*,float*,int,int,int,int,int,int,int);
int main(){
    unsigned state=17;
    auto random=[&state](){state=1664525u*state+1013904223u;return (float(int(state%20001)-10000))/5000.f;};
    for(int size: {6,12,24,48,96})for(int stride:{1,2}){
        const int c=3,h=size,w=size,oh=size/stride,ow=oh,pad=stride==1?2:1;
        std::vector<float>x(294912),k(6400),b(256),out(294914,-999.f);
        for(auto &v:x)v=random();for(auto &v:k)v=random();for(auto &v:b)v=random();
        if(palm_depthwise(x.data(),k.data(),b.data(),out.data()+1,c,h,w,oh,ow,stride,pad))return 1;
        for(int ch=0;ch<c;++ch)for(int y=0;y<oh;++y)for(int xx=0;xx<ow;++xx){
            float ref=b[ch];
            for(int ky=0;ky<5;++ky)for(int kx=0;kx<5;++kx){
                int iy=y*stride+ky-pad,ix=xx*stride+kx-pad;
                if(iy>=0 && iy<h && ix>=0 && ix<w)ref+=x[(ch*h+iy)*w+ix]*k[ch*25+ky*5+kx];
            }
            float got=out[1+(ch*oh+y)*ow+xx];
            if(!std::isfinite(got)||fabs(got-ref)>1e-4f){printf("FAIL %d %d %g %g\n",size,stride,got,ref);return 2;}
        }
        if(out.front()!=-999.f||out[1+c*oh*ow]!=-999.f)return 3;
        printf("PASS size=%d stride=%d\n",size,stride);
    }
    std::vector<float>x(294912,1),k(6400),b(256),output(294912,123);
    if(palm_depthwise(x.data(),k.data(),b.data(),output.data(),257,1,1,1,1,1,2)!=-1||output[0]!=123)return 4;
    return 0;
}
