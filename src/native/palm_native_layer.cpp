#include <opencv2/dnn.hpp>
#include <opencv2/dnn/layer.details.hpp>
#include <opencv2/core.hpp>

// The OpenCV C++ layer hands existing Mat storage to a synchronous callback.
// It avoids the Python custom-layer wrapper's per-layer array conversions.
using Callback=int(*)(int,const float*,float*,int,int,int,int,int,int,int);
static Callback dispatch=nullptr;
class PalmDepthwiseLayer final:public cv::dnn::Layer {
    int key,stride,pad,oh,ow;
public:
    explicit PalmDepthwiseLayer(const cv::dnn::LayerParams& p):Layer(p),
        key(p.get<int>("layer_id")),stride(p.get<int>("stride")),
        pad(p.get<int>("pad")),oh(p.get<int>("out_h")),ow(p.get<int>("out_w")) {}
    static cv::Ptr<cv::dnn::Layer> create(cv::dnn::LayerParams& p) {
        return cv::makePtr<PalmDepthwiseLayer>(p);
    }
    bool getMemoryShapes(const std::vector<cv::dnn::MatShape>& inputs,int,
                         std::vector<cv::dnn::MatShape>& outputs,
                         std::vector<cv::dnn::MatShape>& internals) const override {
        CV_Assert(inputs.size()==1 && inputs[0].size()==4 && inputs[0][0]==1);
        outputs={{1,inputs[0][1],oh,ow}};internals.clear();return false;
    }
    void forward(cv::InputArrayOfArrays inputArrays,cv::OutputArrayOfArrays outputArrays,
                 cv::OutputArrayOfArrays) override {
        std::vector<cv::Mat> inputs,outputs;
        inputArrays.getMatVector(inputs);outputArrays.getMatVector(outputs);
        CV_Assert(dispatch && inputs.size()==1 && outputs.size()==1);
        const auto& x=inputs[0];auto& y=outputs[0];
        CV_Assert(x.dims==4 && x.size[0]==1 && x.type()==CV_32F && x.isContinuous());
        CV_Assert(y.type()==CV_32F && y.isContinuous());
        if(dispatch(key,x.ptr<float>(),y.ptr<float>(),x.size[1],x.size[2],x.size[3],oh,ow,stride,pad)!=0)
            CV_Error(cv::Error::StsError,"Palm PL dispatch failed; inspect driver diagnostic");
    }
};
extern "C" void register_palm_layer(Callback callback) {
    dispatch=callback;
    cv::dnn::LayerFactory::registerLayer("PynqDepthwise",PalmDepthwiseLayer::create);
}
