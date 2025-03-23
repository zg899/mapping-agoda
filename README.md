# mapping-agoda

通过 Google 搜索 + Agoda 页面结构化数据提取 + 智能规则匹配，自动识别本地酒店与 Agoda 酒店是否为同一家，完成酒店聚合与数据比对任务。

## ✨ 项目目标

- 根据本地酒店名称，搜索对应 Agoda 页面
- 提取 Agoda 页面中的 meta 信息（酒店名、地址、邮编、经纬度、酒店ID、标准URL、图片）
- 与本地酒店信息（名称、地址、邮编、经纬度等）进行对比
- 使用规则 + GPT 混合方式判断是否为同一家酒店
- 导出匹配成功的 Agoda 酒店数据至 CSV

## 🔧 功能特性

- ✅ 自动 Google 搜索并获取 Agoda 链接
- ✅ 提取 Agoda 页面 meta 信息（结构化）
- ✅ 名称、地址、经纬度、邮编多维度匹配
- ✅ 阶段性判断策略（80%规则匹配 + 15% GPT + 5%人工）
- ✅ 支持结果导出为 CSV 文件
- 🔜 可扩展：支持 Booking、Trip.com、Yanolja 等平台聚合

## 🗂️ 项目结构（开发中）

mapping-agoda/
├── data/                  # 本地酒店数据（CSV）
├── agoda_scraper.py       # Agoda 页面信息提取模块
├── matcher.py             # 匹配逻辑（规则 + GPT 辅助）
├── utils.py               # 公共函数（相似度、距离计算等）
├── config.env             # API Key 和参数配置
├── main.py                # 主程序入口
└── README.md              # 项目说明文件


## ⚙️ 技术栈

- Python 3.x
- requests + BeautifulSoup4
- pandas
- Google Custom Search API
- OpenAI GPT API（可选）

## 📌 判断逻辑简述

- **阶段 1~2**：名称、地址、经纬度、邮编完全或高度一致 → 自动匹配
- **阶段 3**：部分字段相似但不完全一致 → 调用 GPT 进行辅助判断
- **阶段 4~7**：信息不足或矛盾 → 进入人工审核列表

## 🚀 使用方法（开发中）

```bash
# 安装依赖
pip install -r requirements.txt

# 配置 API Key（Google 搜索 + GPT）
cp config.env.example config.env
# 编辑 config.env 填入你的 API Key

# 执行主程序
python main.py

项目地址：https://github.com/zg899/py/mapping-agoda