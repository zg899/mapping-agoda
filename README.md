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



# Mapping Agoda Project (from Grok3)

## Overview
The **Mapping Agoda** project aims to map a local hotel dataset (stored in an Excel file) to hotel data on Agoda.com by collecting relevant information from Agoda, comparing it with the local dataset, and determining if they refer to the same hotel. The matching process will use a combination of automated similarity checks, GPT-based fuzzy matching, and final human review to ensure accuracy.

## Project Goals
1. **Data Collection**:
   - Use Google Search (`site:agoda.com hotelname`) to find the top 1-3 Agoda links for each hotel in the local dataset.
   - Scrape the following information from each Agoda hotel page:
     - Hotel ID (e.g., `10564991` from `<meta name="twitter:image" content="...">`)
     - Standard hotel URL (e.g., `https://www.agoda.com/nikko-kanaya-hotel-h10564991/hotel/nikko-jp.html`)
     - Latitude and longitude (e.g., from `<meta property="place:location:latitude">`)
     - Address and postal code (e.g., from `<meta property="og:street_address">` and `<meta property="og:postal_code">`)

2. **Data Matching**:
   - Compare the scraped Agoda data with the local dataset using the following criteria:
     - **Name Similarity**: Compare hotel names using string similarity algorithms (e.g., Levenshtein distance).
     - **Address Similarity**: Compare addresses by breaking them into components or using NLP techniques.
     - **Geographic Distance**: Calculate the distance between the two sets of latitude/longitude coordinates using the Haversine formula.
     - **Postal Code**: Check if postal codes match.
   - Use a weighted scoring system to determine the likelihood of a match (e.g., higher weight for geographic distance and postal code).

3. **Fuzzy Matching with GPT**:
   - For cases where automated matching is uncertain, use a GPT API (e.g., OpenAI) to perform semantic analysis and fuzzy matching on names, addresses, and other fields.

4. **Human Review**:
   - Present the matching results to a human reviewer for final validation, potentially with a simple UI showing the comparison details (e.g., names, addresses, distances, and a map visualization).

## Local Dataset
The local dataset is stored in an Excel file (`hotels.xlsx`) with the following columns:
- `code`: Unique hotel code (e.g., `KR03206`)
- `name_en`: Hotel name in English (e.g., `Hotel AirCity Jeju`)
- `country_code`: Country code (e.g., `410` for South Korea, `392` for Japan)
- `area_en`: Area name in English (e.g., `Jeju`)
- `latitude`: Latitude of the hotel (e.g., `33.48960876`)
- `longitude`: Longitude of the hotel (e.g., `126.4925766`)
- `addr_en`: Address in English (e.g., `56, Sammu-ro, Jeju-si, Jeju-do, Republic of Korea`)
- `postal_code`: Postal code (e.g., `63124`)

## Technical Stack
- **Programming Language**: Python
- **Libraries**:
  - `requests` and `BeautifulSoup` (or `Selenium`) for web scraping
  - `pandas` for handling Excel data and data processing
  - `fuzzywuzzy` or `difflib` for string similarity
  - `geopy` for calculating geographic distances
  - OpenAI API (or similar) for GPT-based fuzzy matching
  - `Flask` or `tkinter` (optional) for a simple UI for human review
- **Data Storage**: CSV or SQLite for storing scraped data and matching results

## Development Plan
1. **Phase 1: Data Collection**
   - Develop a script to search Google for Agoda links using hotel names.
   - Scrape the required information (hotel ID, URL, latitude, longitude, address, postal code) from Agoda pages.
   - Save the scraped data into a structured format (e.g., CSV or SQLite).

2. **Phase 2: Data Matching**
   - Implement algorithms to compare the local dataset with the scraped Agoda data:
     - Name and address similarity using string matching.
     - Geographic distance using the Haversine formula.
     - Postal code comparison.
   - Assign weights to each criterion and calculate a confidence score for each match.

3. **Phase 3: Fuzzy Matching with GPT**
   - Integrate a GPT API to handle uncertain matches.
   - Use GPT to analyze semantic similarity between names, addresses, and other fields.

4. **Phase 4: Human Review**
   - Develop a simple interface (CLI or web-based) to display matching results.
   - Allow human reviewers to confirm or reject matches, with details like names, addresses, distances, and a map visualization.

5. **Phase 5: Testing and Optimization**
   - Test the pipeline with a small subset of data (e.g., 10 hotels).
   - Optimize the matching algorithm and GPT prompts based on test results.
   - Scale up to process the entire dataset.

## Potential Challenges
- **Google Search Limits**: Frequent searches may lead to IP bans or CAPTCHA challenges.
  - **Solution**: Use Google Search API or implement delays and proxy rotation.
- **Agoda Page Changes**: Changes in Agoda's page structure may break the scraper.
  - **Solution**: Add error handling and regularly update the scraping logic.
- **Matching Accuracy**: Variations in hotel names and addresses may lead to false positives/negatives.
  - **Solution**: Use multiple matching criteria and GPT for fuzzy matching.
- **Legal Concerns**: Scraping Agoda may violate their Terms of Service.
  - **Solution**: Explore official APIs or ensure ethical scraping practices (e.g., rate limiting).

## Next Steps
- Set up the project environment (install dependencies, create a virtual environment).
- Develop the initial scraping script and test it with a few hotels.
- Share progress and iterate based on feedback.

## Contributing
Feel free to contribute by submitting issues, pull requests, or suggestions. Let's make this project a success together!
