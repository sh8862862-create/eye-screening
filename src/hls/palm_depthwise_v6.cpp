#include <stdint.h>
static void accumulate_row(float plane[10000],float coeff[25],float result[96],
                           unsigned short row_offset, unsigned char count,
                           bool twice, unsigned char pad) {
#pragma HLS INLINE off
#pragma HLS ARRAY_PARTITION variable=plane cyclic factor=32
#pragma HLS ARRAY_PARTITION variable=coeff complete
    for(unsigned char x=0;x<count;++x){
#pragma HLS PIPELINE II=1
#pragma HLS LOOP_FLATTEN off
#pragma HLS LOOP_TRIPCOUNT min=3 max=96
        unsigned short at=row_offset+(twice ? x*2 : x)+2-pad;
        float sum=result[x];
        for(int k=0;k<25;++k){
#pragma HLS UNROLL
            sum += plane[at+(k/5)*100+(k%5)]*coeff[k];
        }
        result[x]=sum;
    }
}
// Reusable NCHW 5x5 depthwise kernel. One channel at a time, five taps parallel.
// Runtime weights/shape allow all 23 palm depthwise layers to share this core.
extern "C" int palm_depthwise(const float *input, const float *weights,
                              const float *bias, float *output,
                              int channels, int height, int width,
                              int out_h, int out_w, int stride, int pad) {
#pragma HLS INTERFACE m_axi port=input offset=slave bundle=gmem depth=294912 max_read_burst_length=64
#pragma HLS INTERFACE m_axi port=weights offset=slave bundle=gmem depth=6400 max_read_burst_length=64
#pragma HLS INTERFACE m_axi port=bias offset=slave bundle=gmem depth=256
#pragma HLS INTERFACE m_axi port=output offset=slave bundle=gmem depth=294912 max_write_burst_length=64
#pragma HLS INTERFACE s_axilite port=input bundle=control
#pragma HLS INTERFACE s_axilite port=weights bundle=control
#pragma HLS INTERFACE s_axilite port=bias bundle=control
#pragma HLS INTERFACE s_axilite port=output bundle=control
#pragma HLS INTERFACE s_axilite port=channels bundle=control
#pragma HLS INTERFACE s_axilite port=height bundle=control
#pragma HLS INTERFACE s_axilite port=width bundle=control
#pragma HLS INTERFACE s_axilite port=out_h bundle=control
#pragma HLS INTERFACE s_axilite port=out_w bundle=control
#pragma HLS INTERFACE s_axilite port=stride bundle=control
#pragma HLS INTERFACE s_axilite port=pad bundle=control
#pragma HLS INTERFACE s_axilite port=return bundle=control
    if(channels<1 || channels>256 || height<1 || height>96 || width<1 || width>96 ||
       out_h<1 || out_h>96 || out_w<1 || out_w>96 ||
       channels*height*width>294912 || channels*out_h*out_w>294912 ||
       (stride!=1 && stride!=2) || pad<0 || pad>2 ||
       (out_w-1)*stride+4-pad>width+1 || (out_h-1)*stride+4-pad>height+1)
        return -1;
    float plane[10000], raw[96], coeff[25], result[96], taps[25];
#pragma HLS ARRAY_PARTITION variable=plane cyclic factor=32
#pragma HLS ARRAY_PARTITION variable=taps complete
#pragma HLS ARRAY_PARTITION variable=coeff complete
    for(int c=0;c<channels;++c){
#pragma HLS LOOP_TRIPCOUNT min=32 max=256
        float b=bias[c];
        for(int k=0;k<25;++k){
#pragma HLS PIPELINE II=1
            coeff[k]=weights[c*25+k];
        }
        // One burst read per input row; reuse all samples from on-chip RAM.
        for(int y=0;y<height+4;++y){
#pragma HLS LOOP_TRIPCOUNT min=10 max=100
            if(y>=2 && y<height+2){
                for(int x=0;x<width;++x){
#pragma HLS PIPELINE II=1
#pragma HLS LOOP_TRIPCOUNT min=6 max=96
                    raw[x]=input[(c*height+y-2)*width+x];
                }
            }
            for(int x=0;x<width+4;++x){
#pragma HLS PIPELINE II=1
#pragma HLS LOOP_TRIPCOUNT min=10 max=100
                plane[y*100+x]=(y>=2 && y<height+2 && x>=2 && x<width+2)?raw[x-2]:0.f;
            }
        }
        for(int y=0;y<out_h;++y){
#pragma HLS LOOP_TRIPCOUNT min=6 max=96
            for(int x=0;x<out_w;++x){
#pragma HLS PIPELINE II=1
#pragma HLS LOOP_TRIPCOUNT min=6 max=96
                result[x]=b;
            }
            for(int ky=0;ky<5;ky+=5){
#pragma HLS PIPELINE off
                for(int k=0;k<25;++k){
#pragma HLS UNROLL
                    taps[k]=(ky*5+k<25)?coeff[ky*5+k]:0.f;
                }
                unsigned short row_offset=(y*stride+ky-pad+2)*100;
                accumulate_row(plane,taps,result,row_offset,(unsigned char)out_w,stride==2,(unsigned char)pad);
            }
            for(int x=0;x<out_w;++x){
#pragma HLS PIPELINE II=1
#pragma HLS LOOP_TRIPCOUNT min=6 max=96
                output[(c*out_h+y)*out_w+x]=result[x];
            }
        }
    }
    return 0;
}
