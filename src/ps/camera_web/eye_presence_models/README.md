# 眼部存在性检测模型

两个 XML 来自 `opencv-python-headless==4.10.0.84` 的 `cv2/data/`：

- `haarcascade_eye.xml`：眼部 Haar 级联。
- `haarcascade_eye_tree_eyeglasses.xml`：兼容部分眼镜和眼睑外观的眼部 Haar 级联。

文件未修改，版权、作者与许可证保留在 XML 文件头。运行时使用板卡已有的 OpenCV `CascadeClassifier`，不替换已有 DNN 运行库。两个模型随网页服务部署到 `camera_web/eye_presence_models/`，不依赖系统安装目录是否包含模型数据。

网页服务的 NEON 精简 OpenCV 缺少该接口时，使用独立进程调用板卡系统 OpenCV。仅子进程移除 NEON 路径覆盖，保留主网页和手势运行库；部署测试必须带上服务环境中的 `PYTHONPATH`、`LD_LIBRARY_PATH`，不能只测普通 SSH 终端。

输入缩放到最长边 320 像素，检查四个直角方向并增加边缘留白。此检测仅用于照片分析前的眼部存在性初筛，不承担疾病分类、活体判断、屏幕翻拍识别或完整图像质量评价。复杂纹理仍可能误检，遮挡、离焦和极端姿态可能漏检。
