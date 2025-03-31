from dotenv import load_dotenv
import os
import pandas as pd
from bs4 import BeautifulSoup
from fuzzywuzzy import fuzz
from geopy.distance import geodesic
from googleapiclient.discovery import build
from pyppeteer import launch
import asyncio
import time
import openai
import re
import random
import logging
from datetime import datetime
import glob
import redis
import json

# 配置日志
logging.basicConfig(
    filename="unmatched_hotels.log",
    level=logging.INFO,
    format="%(asctime)s - %(message)s",
    encoding="utf-8"
)
selenium_logger = logging.getLogger('selenium')
selenium_handler = logging.FileHandler('selenium_errors.log')
selenium_handler.setLevel(logging.INFO)
selenium_handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
selenium_logger.addHandler(selenium_handler)

# 加载 .env 文件
load_dotenv()

# 从环境变量中读取敏感数据
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
GOOGLE_CSE_ID = os.getenv("GOOGLE_CSE_ID")
openai.api_key = os.getenv("OPENAI_API_KEY")

# 检查是否成功读取
if not GOOGLE_API_KEY or not GOOGLE_CSE_ID:
    raise ValueError("Missing GOOGLE_API_KEY or GOOGLE_CSE_ID in environment variables.")
if not openai.api_key:
    raise ValueError("Missing OPENAI_API_KEY in environment variables.")

# Redis 配置
REDIS_HOST = "localhost"
REDIS_PORT = 6379
REDIS_DB = 0
PROXY_SCORE_KEY = "proxy_scores"
INITIAL_PROXY_SCORE = 5
MIN_PROXY_SCORE = -3

redis_client = redis.StrictRedis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB, decode_responses=True)

# PC 端 User-Agent 列表
PC_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/109.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7; rv:108.0) Gecko/20100101 Firefox/108.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/108.0.1462.76 Safari/537.36",
]

# 读取酒店 code 列表
def load_hotel_codes(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        codes = [line.strip() for line in f if line.strip()]
    return codes

# 根据酒店 code 从 Excel 文件中筛选酒店数据
def load_hotel_data(file_path, hotel_codes):
    df = pd.read_excel(file_path)
    df = df[df['code'].isin(hotel_codes)]
    return df.to_dict('records')

# 检查 Redis 代理池
def check_proxies_in_redis():
    proxies = redis_client.hgetall(PROXY_SCORE_KEY)
    if not proxies:
        logging.warning("Proxy pool is empty! Please check if proxies file is loaded correctly.")
    else:
        logging.info(f"Proxy pool loaded with {len(proxies)} proxies.")
    return len(proxies) > 0

# 从文件加载代理并存入 Redis
def load_proxies_from_file(filename):
    logging.info(f"Loading proxies from file: {filename}")
    try:
        with open(filename, 'r', encoding='utf-8') as file:
            proxies = [line.strip() for line in file if line.strip()]
        
        for proxy in proxies:
            if not redis_client.hexists(PROXY_SCORE_KEY, proxy):
                redis_client.hset(PROXY_SCORE_KEY, proxy, INITIAL_PROXY_SCORE)
        
        logging.info(f"Successfully loaded {len(proxies)} proxies.")
        return proxies
    except Exception as e:
        logging.error(f"Failed to load proxies from file: {e}")
        raise e

# 解析代理信息
def parse_proxy_info(proxy_info):
    parts = proxy_info.split(":")
    if len(parts) == 5:
        return parts[0], parts[1], parts[2], parts[3], parts[4]
    else:
        logging.error(f"Invalid proxy info format: {proxy_info}")
        raise ValueError("Invalid proxy info format")

# 测试代理可用性（优化响应时间）
async def check_proxy(proxy_host, proxy_port, proxy_user, proxy_pass):
    browser = None
    try:
        browser = await launch(
            args=[
                '--no-sandbox',
                '--disable-setuid-sandbox',
                f'--proxy-server={proxy_host}:{proxy_port}'
            ],
            headless=True
        )
        logging.info("浏览器启动成功")

        page = await browser.newPage()
        if not page or page.isClosed():
            logging.error("页面创建失败或已关闭，跳过代理检查")
            return False

        await page.authenticate({'username': proxy_user, 'password': proxy_pass})
        logging.info(f"代理认证设置成功，代理: {proxy_host}:{proxy_port}")

        try:
            logging.info("开始检查代理 IP...")
            start_time = time.time()
            response = await asyncio.wait_for(
                page.goto('http://httpbin.org/ip', {'timeout': 30000}),
                timeout=35
            )
            if not response.ok:
                logging.warning(f"代理 IP 检查返回非 200 状态码: {response.status}")
                return False

            ip_info = await page.evaluate('() => document.body.textContent')
            elapsed_time = time.time() - start_time
            if elapsed_time > 10:  # 如果响应时间超过 10 秒，认为代理太慢
                logging.warning(f"代理 {proxy_host}:{proxy_port} 响应太慢 ({elapsed_time:.2f}秒)，跳过")
                return False
            logging.info(f"代理 IP 信息: {ip_info}, 响应时间: {elapsed_time:.2f}秒")
            return True
        except Exception as e:
            logging.error(f"加载代理 IP 页面时发生错误: {e}")
            return False
    except Exception as e:
        logging.error(f"代理测试失败: {e}")
        return False
    finally:
        if browser:
            try:
                await browser.close()
                logging.info("浏览器关闭成功")
            except Exception as close_error:
                logging.warning(f"关闭浏览器时发生异常: {close_error}")

# 选择最佳代理
async def get_best_proxy():
    proxy_scores = redis_client.hgetall(PROXY_SCORE_KEY)
    if not proxy_scores:
        raise ValueError("No proxies available in Redis.")
    
    valid_proxies = {proxy: int(score) for proxy, score in proxy_scores.items() if int(score) >= MIN_PROXY_SCORE}
    if not valid_proxies:
        logging.warning("All proxies have low scores, resetting proxy scores.")
        redis_client.delete(PROXY_SCORE_KEY)
        return None
    
    sorted_proxies = sorted(valid_proxies.items(), key=lambda x: x[1], reverse=True)
    for proxy, score in sorted_proxies:
        proxy_host, proxy_port, proxy_user, proxy_pass, _ = parse_proxy_info(proxy)
        if await check_proxy(proxy_host, proxy_port, proxy_user, proxy_pass):
            logging.info(f"Selected proxy: {proxy} (Score: {score})")
            return proxy
    logging.warning("No working proxies found, resetting proxy scores.")
    redis_client.delete(PROXY_SCORE_KEY)
    return None

# 更新代理分数
def update_proxy_score(proxy, success):
    score = int(redis_client.hget(PROXY_SCORE_KEY, proxy) or INITIAL_PROXY_SCORE)
    if success:
        score += 1
    else:
        score -= 2
        if score < MIN_PROXY_SCORE:
            redis_client.hdel(PROXY_SCORE_KEY, proxy)
            logging.info(f"Proxy {proxy} disabled due to low score.")
            return
    redis_client.hset(PROXY_SCORE_KEY, proxy, score)
    logging.info(f"Proxy {proxy} score updated to {score}")

# 加载相似度规则
def load_similarity_rules(json_file="similarity_rules.json"):
    if not os.path.exists(json_file):
        default_rules = {
            "name": {
                "remove_words": ["hotel"],
                "remove_suffixes": [
                    "by best western signature collection",
                    "by best western",
                    "best western",
                    "signature collection"
                ],
                "remove_cities": [
                    "jeju", "seoul", "busan", "downtown jeju", "hongdae",
                    "myeong-dong", "dongdaemun", "gangseo", "songpa",
                    "jung-gu", "mapo-gu", "haeundae-gu"
                ],
                "replace": {"-": "", " ": ""}
            },
            "address": {
                "remove_words": ["room"],
                "remove_regions": [
                    "mapo-gu", "jung-gu", "songpa", "gangseo", "haeundae-gu",
                    "dongdaemun", "hongdae", "downtown jeju", "jeju-si",
                    "busan", "seoul", "korea", "south korea", "jeju-do"
                ],
                "replace": {"-": " ", "  ": " "}
            }
        }
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(default_rules, f, ensure_ascii=False, indent=4)
        logging.info(f"Created default similarity rules file: {json_file}")
    with open(json_file, 'r', encoding='utf-8') as f:
        return json.load(f)

# 使用 GPT 更新相似度规则
def update_similarity_rules_with_gpt(str1, str2, is_address=False, json_file="similarity_rules.json"):
    try:
        prompt = f"""
Compare the following {'address' if is_address else 'name'} strings to determine if they refer to the same {'address' if is_address else 'hotel'}:
String 1: {str1}
String 2: {str2}
If they are the same, identify any redundant words or patterns that can be removed to improve similarity matching.
Return your response in JSON format:
{{
  "is_same": boolean,
  "redundant_words": [string],
  "redundant_patterns": [string]
}}
"""
        response = openai.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are an assistant that analyzes strings and suggests rules for similarity matching."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=150,
            temperature=0.5
        )
        result = json.loads(response.choices[0].message.content.strip())
        
        if result["is_same"]:
            rules = load_similarity_rules(json_file)
            key = "address" if is_address else "name"
            valid_words = []
            for word in result["redundant_words"]:
                if word and re.match(r'^[a-zA-Z0-9\s\-\']+$', word):  # 只允许字母、数字、空格、连字符和单引号
                    valid_words.append(word)
                else:
                    logging.warning(f"Invalid redundant word skipped: {word}")
            for word in valid_words:
                if word not in rules[key]["remove_words"]:
                    rules[key]["remove_words"].append(word)
            for pattern in result["redundant_patterns"]:
                if pattern and re.match(r'^[a-zA-Z0-9\s\-\*\+]+$', pattern):  # 简单验证正则模式
                    if pattern not in rules[key].get("remove_patterns", []):
                        rules[key].setdefault("remove_patterns", []).append(pattern)
                else:
                    logging.warning(f"Invalid redundant pattern skipped: {pattern}")
            with open(json_file, 'w', encoding='utf-8') as f:
                json.dump(rules, f, ensure_ascii=False, indent=4)
            logging.info(f"Updated {json_file} with GPT suggestions: {result}")
        return result["is_same"]
    except Exception as e:
        logging.error(f"Error updating similarity rules with GPT: {e}")
        return False

# 名称和地址相似度计算（修复正则错误）
def calculate_similarity(str1, str2, is_address=False, rules_file="similarity_rules.json"):
    if not str1 or not str2:
        return 0
    
    str1 = str1.lower().strip()
    str2 = str2.lower().strip()
    rules = load_similarity_rules(rules_file)
    key = "address" if is_address else "name"

    for word in rules[key]["remove_words"]:
        if not word or not re.match(r'^[a-zA-Z0-9\s\-\']+$', word):  # 验证 word 是否有效
            logging.warning(f"Skipping invalid remove_word: {word}")
            continue
        try:
            str1 = re.sub(rf'\b{re.escape(word)}\b\s*', '', str1, flags=re.IGNORECASE)
            str2 = re.sub(rf'\b{re.escape(word)}\b\s*', '', str2, flags=re.IGNORECASE)
        except re.error as e:
            logging.error(f"Regex error with word '{word}': {e}")
            continue
    
    if not is_address:
        for suffix in rules[key]["remove_suffixes"]:
            if not suffix or not re.match(r'^[a-zA-Z0-9\s\-\']+$', suffix):
                logging.warning(f"Skipping invalid suffix: {suffix}")
                continue
            try:
                str1 = re.sub(rf'\b{re.escape(suffix)}\b', '', str1, flags=re.IGNORECASE)
                str2 = re.sub(rf'\b{re.escape(suffix)}\b', '', str2, flags=re.IGNORECASE)
            except re.error as e:
                logging.error(f"Regex error with suffix '{suffix}': {e}")
                continue
        for city in rules[key]["remove_cities"]:
            if not city or not re.match(r'^[a-zA-Z0-9\s\-\']+$', city):
                logging.warning(f"Skipping invalid city: {city}")
                continue
            try:
                str1 = re.sub(rf'\b{re.escape(city)}\b', '', str1, flags=re.IGNORECASE)
                str2 = re.sub(rf'\b{re.escape(city)}\b', '', str2, flags=re.IGNORECASE)
            except re.error as e:
                logging.error(f"Regex error with city '{city}': {e}")
                continue
    else:
        for region in rules[key]["remove_regions"]:
            if not region or not re.match(r'^[a-zA-Z0-9\s\-\']+$', region):
                logging.warning(f"Skipping invalid region: {region}")
                continue
            try:
                str1 = re.sub(rf',\s*{re.escape(region)}', '', str1, flags=re.IGNORECASE)
                str2 = re.sub(rf',\s*{re.escape(region)}', '', str2, flags=re.IGNORECASE)
            except re.error as e:
                logging.error(f"Regex error with region '{region}': {e}")
                continue

    for old, new in rules[key]["replace"].items():
        str1 = str1.replace(old, new)
        str2 = str2.replace(old, new)

    similarity = fuzz.token_sort_ratio(str1, str2)
    
    if similarity < 50:
        if update_similarity_rules_with_gpt(str1, str2, is_address, rules_file):
            similarity = 100
    
    return similarity

# 使用 Google Custom Search API 搜索 Agoda 链接
def search_agoda_hotel(hotel_name):
    try:
        service = build("customsearch", "v1", developerKey=GOOGLE_API_KEY)
        query = f"site:agoda.com {hotel_name}"
        res = service.cse().list(q=query, cx=GOOGLE_CSE_ID, num=10).execute()
        links = [item['link'] for item in res.get('items', [])]
        print(f"Search results for '{hotel_name}': {links}")
        
        for link in links[:3]:
            if '/hotel/' in link and not any(x in link for x in ['/maps/', '/city/', '/reviews/']):
                print(f"Found hotel URL in first 3: {link}")
                return [link]
        
        for link in links[3:]:
            if '/hotel/' in link and not any(x in link for x in ['/maps/', '/city/', '/reviews/']):
                print(f"Found hotel URL in remaining 7: {link}")
                return [link]
        
        print("No hotel URLs found in the search results.")
        return []
    except Exception as e:
        print(f"Error searching for {hotel_name}: {e}")
        return []

# 使用 Pyppeteer 爬取 Agoda 页面信息
async def scrape_agoda_page_pyppeteer(url, proxy=None):
    browser = None
    page = None
    for attempt in range(3):
        try:
            print(f"Scraping URL with Pyppeteer: {url} (Attempt {attempt + 1})")
            selenium_logger.info(f"Attempting to scrape {url} with proxy {proxy if proxy else 'None'}")
            
            user_agent = random.choice(PC_USER_AGENTS)  # 随机选择 User-Agent
            
            if proxy:
                proxy_host, proxy_port, proxy_user, proxy_pass, _ = parse_proxy_info(proxy)
                browser = await launch(
                    args=[
                        '--no-sandbox',
                        '--disable-setuid-sandbox',
                        f'--proxy-server={proxy_host}:{proxy_port}',
                        f'--user-agent={user_agent}'
                    ],
                    headless=True
                )
                logging.info("浏览器启动成功")
                
                page = await browser.newPage()
                if not page or page.isClosed():
                    logging.error("页面创建失败或已关闭，跳过抓取")
                    return None
                
                await page.authenticate({'username': proxy_user, 'password': proxy_pass})
                logging.info(f"代理认证设置成功，代理: {proxy_host}:{proxy_port}")

                # 获取当前代理 IP
                await page.goto('http://httpbin.org/ip', {'timeout': 30000, 'waitUntil': 'networkidle2'})
                ip_info = await page.evaluate('() => document.body.textContent')
                current_ip = json.loads(ip_info).get('origin', 'Unknown')
                print(f"当前使用的 IP 地址: {current_ip}")
                print(f"当前使用的 User-Agent: {user_agent}")
                logging.info(f"当前使用的 IP 地址: {current_ip}, User-Agent: {user_agent}")
            else:
                browser = await launch(
                    args=['--no-sandbox', '--disable-setuid-sandbox', f'--user-agent={user_agent}'],
                    headless=True
                )
                page = await browser.newPage()
                current_ip = "本地 IP (无代理)"
                print(f"当前使用的 IP 地址: {current_ip}")
                print(f"当前使用的 User-Agent: {user_agent}")
                logging.info(f"当前使用的 IP 地址: {current_ip}, User-Agent: {user_agent}")

            await page.goto(url, {'timeout': 60000, 'waitUntil': 'networkidle2'})  # 超时 60 秒
            await page.waitForSelector("meta[property='og:title']", timeout=30000)
            html = await page.content()
            soup = BeautifulSoup(html, 'html.parser')

            hotel_id = None
            twitter_image = soup.find("meta", {"name": "twitter:image"})
            if twitter_image and "hotelImages" in twitter_image["content"]:
                hotel_id = twitter_image["content"].split("hotelImages/")[1].split("/")[0]
            print(f"Hotel ID: {hotel_id}")

            hotel_url = None
            alternate_link = soup.find("link", {"rel": "alternate", "hreflang": "en"})
            if alternate_link:
                hotel_url = alternate_link["href"]
            print(f"Hotel URL: {hotel_url}")

            latitude = soup.find("meta", {"property": "place:location:latitude"})
            longitude = soup.find("meta", {"property": "place:location:longitude"})
            lat = float(latitude["content"]) if latitude else None
            lon = float(longitude["content"]) if longitude else None
            print(f"Latitude: {lat}, Longitude: {lon}")

            street_address = soup.find("meta", {"property": "og:street_address"})
            region = soup.find("meta", {"property": "og:region"})
            country = soup.find("meta", {"property": "og:country-name"})
            addr_parts = []
            if street_address:
                addr_parts.append(street_address["content"])
            if region:
                addr_parts.append(region["content"])
            if country:
                addr_parts.append(country["content"])
            addr = ", ".join(addr_parts) if addr_parts else None
            print(f"Address: {addr}")

            postal_code = soup.find("meta", {"property": "og:postal_code"})
            postal = postal_code["content"].strip() if postal_code else None
            print(f"Postal Code: {postal}")

            og_title = soup.find("meta", {"property": "og:title"})
            hotel_name = None
            if og_title:
                title = og_title["content"]
                if ", " in title:
                    hotel_name = title.split(", ")[0]
                elif " | " in title:
                    hotel_name = title.split(" | ")[0]
                else:
                    hotel_name = title
            print(f"Hotel Name: {hotel_name}")

            update_proxy_score(proxy, True)
            return {
                "hotel_id": hotel_id,
                "url": hotel_url,
                "latitude": lat,
                "longitude": lon,
                "address": addr,
                "postal_code": postal,
                "hotel_name": hotel_name
            }
        except Exception as e:
            print(f"Error scraping with Pyppeteer {url}: {e}")
            selenium_logger.error(f"Failed to scrape {url}: {str(e)}")
            if proxy:
                update_proxy_score(proxy, False)
                if "timeout" in str(e).lower() or "connection" in str(e).lower():
                    print(f"Proxy {proxy} failed due to timeout/connection issue, switching to a new one.")
                    return None
            if attempt < 2:
                await asyncio.sleep(10)  # 增加重试间隔到 10 秒
            else:
                print(f"Failed to scrape {url} after 3 attempts.")
                return None
        finally:
            if page and not page.isClosed():
                try:
                    await page.close()
                    logging.info("页面关闭成功")
                except Exception as e:
                    logging.warning(f"关闭页面时发生异常: {e}")
            if browser:
                try:
                    await browser.close()
                    logging.info("浏览器关闭成功")
                except Exception as e:
                    logging.warning(f"关闭浏览器时发生异常: {e}")
    return None

# 计算地理距离（以米为单位）
def calculate_distance(lat1, lon1, lat2, lon2):
    if lat1 and lon1 and lat2 and lon2:
        distance_km = geodesic((lat1, lon1), (lat2, lon2)).kilometers
        return distance_km * 1000
    return float('inf')

# GPT 模糊匹配
def gpt_fuzzy_match(local_data, agoda_data):
    try:
        prompt = f"""
Compare the following hotel data to determine if they refer to the same hotel:
Local Hotel: {local_data['name_en']}
Local Address: {local_data['addr_en']}
Agoda Hotel: {agoda_data['hotel_name']}
Agoda Address: {agoda_data['address']}
Return a similarity score between 0 and 100, where 100 means they are definitely the same hotel, and 0 means they are definitely different. Provide a brief explanation.
Format your response as:
Similarity: <score>
Explanation: <reason>
"""
        response = openai.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a helpful assistant that compares hotel data and provides similarity scores."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=100,
            temperature=0.5
        )
        response_text = response.choices[0].message.content.strip()
        lines = response_text.split("\n")
        for line in lines:
            if line.startswith("Similarity:"):
                similarity = int(line.split(":")[1].strip())
                return similarity
        return 80
    except Exception as e:
        print(f"Error in GPT fuzzy match: {e}")
        return 80

# 生成时间戳
def generate_timestamp():
    return datetime.now().strftime("%Y%m%d_%H%M%S")

# 导出批次结果
def export_batch_results(batch_results, batch_num):
    timestamp = generate_timestamp()
    filename = f"matching_results_batch_{batch_num}_{timestamp}.xlsx"
    df = pd.DataFrame(batch_results)
    df.to_excel(filename, index=False)
    print(f"Batch {batch_num} results saved to {filename}")

# 加载已处理酒店 code
def load_processed_codes(log_file):
    if os.path.exists(log_file):
        with open(log_file, 'r', encoding='utf-8') as f:
            return set(line.strip() for line in f)
    return set()

# 记录已处理酒店 code
def log_processed_code(log_file, code):
    with open(log_file, 'a', encoding='utf-8') as f:
        f.write(f"{code}\n")

# 整合所有批次 Excel 文件
def merge_excel_files(pattern, output_file):
    all_files = glob.glob(pattern)
    df_list = [pd.read_excel(file) for file in all_files]
    merged_df = pd.concat(df_list, ignore_index=True)
    merged_df.to_excel(output_file, index=False)
    print(f"All batch results merged into {output_file}")

# 主流程（异步）
async def main():
    file_path = "/Users/zhenggang/python/project/mapping-agoda/hotel_last3m.xlsx"
    hotel_codes_file = "1798kr.txt"
    proxy_file = "../100.txt"
    hotel_codes = load_hotel_codes(hotel_codes_file)
    hotels = load_hotel_data(file_path, hotel_codes)
    proxies = load_proxies_from_file(proxy_file)
    check_proxies_in_redis()
    print(f"Loaded {len(hotels)} hotels from Excel file based on {hotel_codes_file}.")
    print(f"Loaded {len(proxies)} proxies from {proxy_file} into Redis.")

    processed_log_file = "processed_hotels.log"
    processed_codes = load_processed_codes(processed_log_file)

    results = []
    total_time = 0
    gpt_calls = 0
    gcp_calls = 0
    processed_count = 0  # 已处理的酒店计数
    skipped_count = 0   # 跳过的酒店计数
    scrape_count = 0

    batch_size = 100
    batch_results = []
    batch_num = 1

    current_proxy = None

    for hotel in hotels:
        code = hotel['code']
        if code in processed_codes:
            skipped_count += 1
            print(f"Skipping already processed hotel: {code}")
            continue

        start_time = time.time()
        processed_count += 1  # 只在未跳过时递增
        total_processed = processed_count + skipped_count
        print(f"\nProcessing hotel {processed_count}/{len(hotels)} (Total processed: {total_processed}/{len(hotels)}): {hotel['name_en']} (Code: {code})")
        
        if scrape_count % 10 == 0 or scrape_count == 0 or current_proxy is None:
            current_proxy = await get_best_proxy()
            if not current_proxy:
                print("No valid proxies available, exiting.")
                break
            print(f"Switching to new proxy: {current_proxy}")
        
        hotel_links = search_agoda_hotel(hotel['name_en'])
        gcp_calls += 1
        if not hotel_links:
            print("No hotel URLs found, skipping this hotel.")
            result = {
                "local_hotel": hotel['name_en'],
                "code": code,
                "country_code": hotel.get('country_code', ''),
                "area_en": hotel.get('area_en', ''),
                "latitude": hotel.get('latitude', ''),
                "longitude": hotel.get('longitude', ''),
                "addr_en": hotel.get('addr_en', ''),
                "postal_code": hotel.get('postal_code', ''),
                "agoda_hotel": '',
                "name_similarity": 0,
                "address_similarity": 0,
                "distance_m": float('inf'),
                "postal_match": False,
                "postal_needs_review": True,
                "score": 0,
                "match_time_seconds": 0,
                "agoda_hotel_id": '',
                "agoda_url": '',
                "agoda_latitude": '',
                "agoda_longitude": '',
                "agoda_address": '',
                "agoda_postal_code": '',
                "url_found": False
            }
            batch_results.append(result)
            log_processed_code(processed_log_file, code)
            continue

        scrape_count += 1
        agoda_data = await scrape_agoda_page_pyppeteer(hotel_links[0], proxy=current_proxy)
        if not agoda_data:
            print("Failed to scrape Agoda page, skipping this hotel.")
            current_proxy = None  # 强制下次循环切换代理
            result = {
                "local_hotel": hotel['name_en'],
                "code": code,
                "country_code": hotel.get('country_code', ''),
                "area_en": hotel.get('area_en', ''),
                "latitude": hotel.get('latitude', ''),
                "longitude": hotel.get('longitude', ''),
                "addr_en": hotel.get('addr_en', ''),
                "postal_code": hotel.get('postal_code', ''),
                "agoda_hotel": '',
                "name_similarity": 0,
                "address_similarity": 0,
                "distance_m": float('inf'),
                "postal_match": False,
                "postal_needs_review": True,
                "score": 0,
                "match_time_seconds": 0,
                "agoda_hotel_id": '',
                "agoda_url": '',
                "agoda_latitude": '',
                "agoda_longitude": '',
                "agoda_address": '',
                "agoda_postal_code": '',
                "url_found": True
            }
            batch_results.append(result)
            log_processed_code(processed_log_file, code)
            continue

        hotel_id = agoda_data.get('hotel_id') if agoda_data.get('hotel_id') else None

        name_similarity = calculate_similarity(hotel['name_en'], agoda_data['hotel_name'])
        address_similarity = calculate_similarity(hotel['addr_en'].split(',')[0], agoda_data['address'].split(',')[0] if agoda_data['address'] else None, is_address=True)
        distance_m = calculate_distance(hotel['latitude'], hotel['longitude'], agoda_data['latitude'], agoda_data['longitude'])
        postal_match = str(hotel['postal_code']).strip() == str(agoda_data['postal_code']).strip() if agoda_data.get('postal_code') else True
        postal_needs_review = not postal_match if agoda_data.get('postal_code') else False

        score = (0.3 * name_similarity + 0.2 * address_similarity + (0.3 if postal_match else 0)) / 0.8
        if distance_m < 1000:
            score += 30
        elif distance_m > 5000:
            score -= 20

        if 40 < score < 80:
            gpt_score = gpt_fuzzy_match(hotel, agoda_data)
            gpt_calls += 1
            print(f"GPT Fuzzy Match Score: {gpt_score}")
            score = 0.4 * score + 0.6 * gpt_score

        end_time = time.time()
        match_time = end_time - start_time
        total_time += match_time

        result = {
            "local_hotel": hotel['name_en'],
            "code": code,
            "country_code": hotel.get('country_code', ''),
            "area_en": hotel.get('area_en', ''),
            "latitude": hotel.get('latitude', ''),
            "longitude": hotel.get('longitude', ''),
            "addr_en": hotel.get('addr_en', ''),
            "postal_code": hotel.get('postal_code', ''),
            "agoda_hotel": agoda_data['hotel_name'] if agoda_data else '',
            "name_similarity": name_similarity if agoda_data else 0,
            "address_similarity": address_similarity if agoda_data else 0,
            "distance_m": distance_m if agoda_data else float('inf'),
            "postal_match": postal_match if agoda_data else False,
            "postal_needs_review": postal_needs_review if agoda_data else True,
            "score": score if agoda_data else 0,
            "match_time_seconds": match_time,
            "agoda_hotel_id": hotel_id if agoda_data else '',
            "agoda_url": agoda_data['url'] if agoda_data else '',
            "agoda_latitude": agoda_data['latitude'] if agoda_data else '',
            "agoda_longitude": agoda_data['longitude'] if agoda_data else '',
            "agoda_address": agoda_data['address'] if agoda_data else '',
            "agoda_postal_code": agoda_data['postal_code'] if agoda_data else '',
            "url_found": True if agoda_data else False
        }
        batch_results.append(result)
        log_processed_code(processed_log_file, code)

        print(f"Match Time: {match_time:.2f} seconds")
        await asyncio.sleep(1)

        if len(batch_results) == batch_size:
            export_batch_results(batch_results, batch_num)
            batch_results = []
            batch_num += 1

    if batch_results:
        export_batch_results(batch_results, batch_num)

    merge_excel_files("matching_results_batch_*.xlsx", "final_matching_results.xlsx")

    unmatched_hotels = [r for r in results if r['score'] < 80]
    if unmatched_hotels:
        logging.info(f"\nFinal check after processing {processed_count} hotels (skipped {skipped_count}), found {len(unmatched_hotels)} unmatched hotels:")
        for uh in unmatched_hotels:
            logging.info(f"Local Hotel: {uh['local_hotel']}, Address: {uh['addr_en']}, Agoda Hotel: {uh['agoda_hotel']}, Agoda Address: {uh['agoda_address']}, Score: {uh['score']}")
        print(f"Logged {len(unmatched_hotels)} unmatched hotels in total.")

    print("\nMatching Results Summary:")
    print(f"Total Hotels Processed: {processed_count} (Skipped: {skipped_count})")
    print(f"Total Matching Time: {total_time:.2f} seconds")
    avg_time_per_hotel = total_time / processed_count if processed_count else 0
    print(f"Average Time per Hotel: {avg_time_per_hotel:.2f} seconds")

    total_hotels = 1798
    estimated_total_time = avg_time_per_hotel * total_hotels
    print(f"Estimated Time for {total_hotels} Hotels: {estimated_total_time:.2f} seconds ({estimated_total_time / 3600:.2f} hours)")

    gcp_cost_per_1000 = 5
    total_gcp_cost = (gcp_calls / 1000) * gcp_cost_per_1000 * (total_hotels / processed_count) if processed_count else 0
    print(f"Total GCP Calls: {gcp_calls} (for {processed_count} hotels)")
    print(f"Estimated GCP Cost for {total_hotels} Hotels: ${total_gcp_cost:.2f}")

    tokens_per_call = 300
    cost_per_million_tokens_input = 0.50
    cost_per_million_tokens_output = 1.50
    total_tokens = tokens_per_call * gpt_calls * (total_hotels / processed_count) if processed_count else 0
    total_gpt_cost = (total_tokens / 1000000) * (cost_per_million_tokens_input + cost_per_million_tokens_output)
    print(f"Total GPT Calls: {gpt_calls} (for {processed_count} hotels)")
    print(f"Estimated GPT Cost for {total_hotels} Hotels: ${total_gpt_cost:.2f}")

    total_cost = total_gcp_cost + total_gpt_cost
    print(f"Estimated Total Cost for {total_hotels} Hotels: ${total_cost:.2f}")

    return results

if __name__ == "__main__":
    print("Running Version 1.16: Processing hotels from 1798kr.txt with Pyppeteer, improved timeout and regex handling")
    asyncio.run(main())