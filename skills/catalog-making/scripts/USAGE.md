# Catalog 结构检查

使用具备 **PyMuPDF** 的 Python 3；支持 `import pymupdf` 与旧版 `import fitz`。脚本不下载依赖、不修改 PDF，不要求 Pillow。

```sh
python3 scripts/catalog_qc.py --pdf catalog.pdf --manifest manifest.json --out qc.json
python3 scripts/catalog_qc.py --pdf revised.pdf --manifest manifest.json --out revision-qc.json --baseline previous.pdf --allowed-changed-pages '83,84' --required-changed-pages '83'
```

Manifest 示例（坐标单位 PDF point；页码从 1 开始；PyMuPDF 未旋转页面坐标，左上为原点）：

```json
{
  "expected_pages": 12,
  "product_codes": ["BED-001", "BED-001-ALT"],
  "oem_codes": ["OEM-01"],
  "allowed_urls": ["https://example.com", "mailto:sales@example.com"],
  "expected_images": [
    {"page": 3, "bbox": [40, 100, 240, 200], "source_pixel_size": [1200, 600]}
  ]
}
```

- 所有字段均可省略；未提供产品编号时明确警告未检查库存完整性。
- 每个声明编号必须恰好出现一次；边界包含字母、数字、下划线与连字符，避免将 `BED-001-ALT` 误计为 `BED-001`。PDF 必须可提取文字；轮廓字或图片中的编号需要人工核验。
- `allowed_urls` 为严格完整 URI 列表；省略仅报告、不限制目标。空数组禁止全部外部 URI。有效协议为 HTTP、HTTPS、mailto、tel。本工具不联网探测 URL 是否在线或邮箱是否可投递。
- 所有 PDF 光栅图片检查 MediaBox 边界与仿射变换等比性，允许旋转。0.5% 尺度/剪切容差、0.75pt 边界容差用于容纳数值舍入。`expected_images` 的 bbox 是图片本身的放置矩形，**不是外部留白框**，仅适用于无旋转的图片；会匹配实际图片位置并检查源画布宽高比。
- 全部文字跨度越界和不可提取嵌入字体程序作为 warning。默认字体资源可能未使用，Type 3 字体也不一定有独立字体文件，不能据此单独断言字体丢失。
- baseline 按同页号、同一渲染器、最长边 600px 精确比较；只有允许页可变化。页数不一致时明确 FAIL，必须另做 page mapping 后比较，不能将插页后所有页误认成修改。小尺寸渲染不保证检出细微或非可见元数据变化。
- 可选 `--required-changed-pages` 指定必须实际变化的页，防止误交付未更新版本；必须同时提供 baseline，且 required 必须是 allowed 的子集，否则参数检查失败。要求页面在 600px 渲染比较中发生变化，不能保证变化内容符合语义要求。
- 输出 `PASS` / `FAIL`、errors、warnings 与人工复核状态；失败退出码为 1。**PASS 仅指已声明项目的结构检查通过，并非成品批准。** 还必须渲染逐页检查、核对产品原始像素和裁切路径。图片完整 bbox 不能证明没有 clip/mask、源图预裁剪、变色或 AI 重绘。

`layout_helpers.contain_box(source_w, source_h, frame_x, frame_y, frame_w, frame_h)` 返回居中且完整等比的 `(x, y, width, height)`。坐标可以为负，尺寸必须正且所有值有限；调用方仍须检查框位于页面之内。函数不读写或重采样图片。

## 传统抠图母版像素核验

```sh
python3 scripts/verify_cutout.py --source original.png --cutout cutout-master.png --protect-mask product-interior-mask.png --out cutout-qc.json
```

需要 Pillow。三张图必须保持原尺寸；cutout 必须含 alpha。保护蒙版有 alpha 时取 `alpha > 0`，无 alpha 时只接受 1-bit / L 灰度蒙版并取灰度 > 0；选区必须非空。蒙版应由可信制作流程覆盖商品内部，边缘另外人工检查。选区内要求解码后的 RGB 与原图逐像素完全一致，且 cutout alpha 必须为 255；选区外背景 RGB/alpha 不纳入比较。工具不会证明蒙版本身可靠或完整，不能把只保护一小块的通过结果当成整件商品保真。使用未缩放、未有损重压缩的透明母版检查，**不要比较白底 JPEG 交付副本**。异色彩模式按 Pillow RGB 转换后比较，严格色彩管理须另核对源 ICC 与导出设置。
