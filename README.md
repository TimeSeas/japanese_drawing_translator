# 日文零件图纸术语自动翻译系统

> Japanese Mechanical Drawing Terminology Auto-Translator

基于 Python + PaddleOCR + PyQt5 构建的桌面应用，自动识别机械零件图纸中的日文术语并翻译为中文，以红色字体在原图上标注翻译结果。

## ✨ 功能特性

- **📄 多格式支持** — PDF（图像型/扫描件）、JPG、PNG、BMP，保持原始分辨率和色彩空间
- **🔍 高精度 OCR** — PaddleOCR PP-OCRv4 日文专用模型，覆盖不同字体、大小、旋转角度的文本
- **📖 离线术语翻译** — 内置 740+ 条日→中机械术语词典，精确匹配 + 模糊匹配双引擎
- **🎨 翻译标注叠加** — 红色中文译文绘制在原图上，支持**遮挡原文**/不遮挡原文两种模式
- **🚫 不遮挡模式碰撞避免** — 智能放置算法，自动检测并避免多条译文互相覆盖
- **📊 结构化数据存储** — 识别文本、翻译结果、坐标位置以 JSON 格式保存
- **🖥️ 桌面 UI** — PyQt5 图形界面，支持图纸缩放/拖拽预览、结果筛选、词典编辑
- **📝 操作日志** — 按天切割的日志系统，保留 180 天，记录所有检测操作
- **🔒 完全离线** — 所有处理本地执行，图纸数据不联网传输

## 🎬 界面预览

```
┌──────────────────────────────────────────┐
│  菜单栏: 文件 | 词典管理 | 视图 | 帮助      │
├─────────────┬────────────────────────────┤
│  工具栏      │                            │
│ 📂打开 🔍缩放│      图纸预览区域             │
│ 💾导出       │   (支持滚轮缩放 + 拖拽平移)    │
├─────────────┤   红色标注实时叠加显示         │
│  控制栏      │                            │
│ □遮挡原文    │                            │
│ [▶ 执行识别] │                            │
├─────────────┴────────────────────────────┤
│  翻译结果表格                              │
│  ID │ 日文原文 │ 中文翻译 │ 匹配方式 │ 置信度 │
│  1  │ 面取り   │ 倒角     │ 精确     │ 100%  │
│  2  │ 焼入     │ 淬火     │ 精确     │ 100%  │
├──────────────────────────────────────────┤
│  状态栏: 共 56 条 | 已翻译 34 条 | 耗时 4.7s │
└──────────────────────────────────────────┘
```

## 🚀 快速开始

### 环境要求

- Windows 10 / 11 (64-bit)
- Python 3.8+
- 推荐: Intel Core i7 / 16GB RAM / SSD

### 安装

```bash
# 1. 克隆仓库
git clone https://github.com/your-username/japanese-drawing-translator.git
cd japanese-drawing-translator

# 2. 安装依赖
pip install -r requirements.txt

# 3. 启动应用
python main.py
```

### 使用步骤

1. **打开图纸** — 菜单栏 `文件 → 打开文件` 或拖放文件到窗口
2. **执行翻译** — 点击 `▶ 执行识别与翻译` 按钮
3. **查看结果** — 图纸上红色文字即翻译结果，表格查看详情
4. **切换模式** — 勾选/取消 `遮挡原文` 切换标注模式
5. **导出数据** — `文件 → 导出标注图纸` 或 `导出翻译数据 JSON`

## 📁 项目结构

```
japanese_drawing_translator/
├── main.py                    # 应用入口
├── requirements.txt           # Python 依赖清单
├── config.yaml                # 可配置参数
├── dictionary.json            # 日→中机械术语词典 (740+ 条)
├── src/
│   ├── file_parser.py         # 文件解析 (PDF → 图像, 多格式加载)
│   ├── image_processor.py     # 图像预处理 (灰度/降噪/二值化/旋转校正)
│   ├── ocr_engine.py          # OCR 引擎封装 (PaddleOCR + Windows OCR 回退)
│   ├── translator.py          # 术语翻译 (精确匹配 + 模糊匹配)
│   ├── overlay_renderer.py    # 标注叠加 (遮挡/不遮挡 + 碰撞避免)
│   ├── data_store.py          # JSON 存储 + 日志管理
│   └── ui/
│       ├── main_window.py     # 主窗口布局
│       ├── image_viewer.py    # 图纸预览控件 (QGraphicsView)
│       ├── result_panel.py    # 结果表格面板
│       └── dict_editor.py     # 词典编辑器对话框
├── logs/                      # 日志文件 (180天保留)
└── output/                    # 默认输出目录
```

## 🛠 技术栈

| 组件 | 技术 | 说明 |
|------|------|------|
| **OCR 引擎** | PaddleOCR (PP-OCRv4) | 日文检测+识别模型，支持旋转文本 |
| **UI 框架** | PyQt5 | 桌面 GUI，QGraphicsView 图纸预览 |
| **图像处理** | OpenCV + Pillow | 预处理流水线，标注叠加渲染 |
| **文本翻译** | FuzzyWuzzy | 离线术语词典匹配，Levenshtein 距离模糊匹配 |
| **PDF 解析** | pypdf + pdf2image | 多层 PDF 逐页渲染 |
| **数据存储** | JSON + logging | 结构化结果存储，按天切割日志 |

## 📊 性能指标

| 指标 | 要求 | 实测 (i7/16GB/SSD) |
|------|------|---------------------|
| 单图纸处理耗时 | ≤ 5 秒 | ~4-5 秒 (A4 尺寸图纸) |
| 翻译准确率 | ≥ 95% | 词典命中项 > 95% |
| 连续运行稳定性 | 72 小时 | 无内存泄漏 |
| 支持文件格式 | PDF/JPG/PNG/BMP | ✅ 全部支持 |

## 📖 术语词典

词典以 JSON 格式存储，覆盖以下类别：

- **材料相关** — ステンレス → 不锈钢, 超硬 → 硬质合金...
- **加工方法** — 放電加工 → 电火花加工, ワイヤーカット → 线切割...
- **几何特征** — 面取り → 倒角, 座ぐり → 锪孔, テーパ → 锥度...
- **部件名称** — ストリッパー → 卸料板, 入子 → 镶件...
- **公差配合** — はめあい → 配合, すきまばめ → 间隙配合...
- **表面处理** — 焼入 → 淬火, クロムめっき → 镀铬...
- **测量检测** — 三次元測定 → 三次元测量, 全数検査 → 全数检查...
- **图纸术语** — 断面図 → 剖视图, 第三角法 → 第三角投影法...

可在应用中通过 `词典 → 编辑词典` 增删改查词条。

## 🔧 配置说明

编辑 `config.yaml` 调整参数：

```yaml
preprocessing:
  max_dimension: 3000    # 大图最大边长 (超过等比缩放)

ocr:
  lang: "japan"          # PaddleOCR 语言模型
  min_confidence: 0.5    # 最低置信度阈值

translator:
  fuzzy_threshold: 85    # 模糊匹配最低相似度 (%)

overlay:
  text_color: [255, 0, 0]     # 红色标注
  default_cover_mode: true     # 默认遮挡模式
  min_gap: 3                  # 译文间距

logging:
  retention_days: 180   # 日志保留天数
```

## 📄 许可

本项目仅限学习与研究用途。

## 🙏 致谢

- [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) — OCR 识别引擎
- [PyQt5](https://www.riverbankcomputing.com/software/pyqt/) — 桌面 GUI 框架
- [FuzzyWuzzy](https://github.com/seatgeek/fuzzywuzzy) — 模糊字符串匹配
