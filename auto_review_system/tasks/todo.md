# PaddleOCR 数据格式聚类提取表格结构

## 规划执行列表
- [x] 分析 RapidOCR 输出的带有坐标系的数据格式
- [x] 分析 PaddleOCR 在线API vs 本地代码差异（图片返回结论：不需要）
- [x] 修改 `parsers/pdf_parser.py` 中的 `_extract_text_from_rapidocr_result`
- [x] 通过 Y 轴聚类 + X 轴排序算法，恢复表格行列结构
- [x] 生成 Markdown `| cell1 | cell2 |` 格式供下游向量检索匹配

## 验证结果
- ✅ 语法检查通过
- ✅ 模拟 GB50210 表6.5.8 数据测试 — 表格结构完美还原
- ✅ 所有断言通过
- ✅ 不影响 OpenDataLoader 主路径

## 结果复盘
核心问题不在于是否返回图片（在线API的图片只是调试可视化），而是 RapidOCR 的坐标信息被丢弃导致表格结构打散。通过纯计算的坐标聚类算法（零新增依赖）完美解决。
