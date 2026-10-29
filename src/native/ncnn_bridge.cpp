#include "net.h"
#include <cstring>
#include <exception>
#include <memory>
#include <cstdio>

extern "C" void* create_pose_net(const char* param,const char* weights,int threads) {
    try {
        if(threads<1 || threads>2)return nullptr;
        std::unique_ptr<ncnn::Net> net(new ncnn::Net());
        net->opt.num_threads=threads;
        net->opt.use_vulkan_compute=false;
        net->opt.use_fp16_packed=false;net->opt.use_fp16_storage=false;net->opt.use_fp16_arithmetic=false;
        net->opt.use_bf16_storage=false;
        if(net->load_param(param) || net->load_model(weights))return nullptr;
        return net.release();
    } catch(...) {return nullptr;}
}
extern "C" void destroy_pose_net(void* handle){delete static_cast<ncnn::Net*>(handle);}
extern "C" int forward_pose_net(void* handle,const float* input,float* output) {
    if(!handle || !input || !output)return -1;
    try {
        auto* net=static_cast<ncnn::Net*>(handle);
        ncnn::Mat image(224,224,3);
        if(image.empty())return -2;
        for(int c=0;c<3;++c){float* plane=image.channel(c);for(int i=0;i<224*224;++i)plane[i]=input[i*3+c];}
        auto ex=net->create_extractor();
        if(ex.input("input",image))return -3;
        const char* names[]={"Identity","Identity_1","Identity_2","Identity_3"};
        int sizes[]={63,1,1,63},offset=0;
        for(int i=0;i<4;++i){
            ncnn::Mat result;
            int code=ex.extract(names[i],result);
            // Mat::total includes aligned channel padding (63 -> 64 floats).
            if(code || result.dims!=1 || result.w!=sizes[i] || result.elempack!=1 || result.elemsize!=4 || result.total()<static_cast<size_t>(sizes[i])) {
                std::fprintf(stderr,"output %s code=%d dims=%d w=%d h=%d c=%d total=%zu elem=%zu pack=%d\n",names[i],code,result.dims,result.w,result.h,result.c,result.total(),result.elemsize,result.elempack);return -4;
            }
            std::memcpy(output+offset,result.data,sizes[i]*sizeof(float));offset+=sizes[i];
        }
        return 0;
    } catch(...) {return -5;}
}
