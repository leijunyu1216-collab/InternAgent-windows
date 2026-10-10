import sys
import os
import uuid
import time  # 添加time模块用于重试延迟

# 添加本地工具模块路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camel.toolkits import (
    VideoAnalysisToolkit,
    SearchToolkit,
    CodeExecutionToolkit,
    ImageAnalysisToolkit,
    DocumentProcessingToolkit,
    AudioAnalysisToolkit,
    AsyncBrowserToolkit,
    ExcelToolkit,
    FunctionTool
)
from camel.models import ModelFactory
from camel.types import(
    ModelPlatformType,
    ModelType
)
from camel.tasks import Task
from camel.utils import dependencies_required
from dotenv import load_dotenv

load_dotenv(override=True)

import json
import re
from typing import List, Dict, Any, Tuple, Optional
from utils.logger import get_logger
import shutil
from bs4 import BeautifulSoup

# 导入our_tools中的函数
from tools.our_tools import (
    download_media_from_url as original_download_media_from_url, 
    search_wiki_revision, 
    ocr2text,
)

from tools.info_processing_tools import (
    search_and_summarize_webpages,
    search_and_summarize_papers,
    summarize_paper,
    search_academic_papers,
)

logger = get_logger(__name__)


# 创建模型工厂函数，避免重复创建相同模型
def create_models():
    """创建所需的模型实例"""
    web_model = ModelFactory.create(
        model_platform=ModelPlatformType.OPENAI,
        model_type=ModelType.GPT_4O,
        model_config_dict={"temperature": 0},
    )
    
    document_processing_model = ModelFactory.create(
        model_platform=ModelPlatformType.OPENAI,
        model_type=ModelType.GPT_4O_MINI,
        model_config_dict={"temperature": 0},
    )
    
    reasoning_model = ModelFactory.create(
        model_platform=ModelPlatformType.OPENAI,
        model_type=ModelType.O3_MINI,
        model_config_dict={"temperature": 0},
    )
    
    image_analysis_model = ModelFactory.create(
        model_platform=ModelPlatformType.OPENAI,
        model_type=ModelType.GPT_4O,
        model_config_dict={"temperature": 0},
    )
    
    audio_reasoning_model = ModelFactory.create(
        model_platform=ModelPlatformType.OPENAI,
        model_type=ModelType.O4_MINI,
        model_config_dict={"temperature": 0},
    )
    
    web_agent_model = ModelFactory.create(
        model_platform=ModelPlatformType.OPENAI,
        model_type=ModelType.GPT_4O,
        model_config_dict={"temperature": 0},
    )
    
    planning_agent_model = ModelFactory.create(
        model_platform=ModelPlatformType.OPENAI,
        model_type=ModelType.O3_MINI,
        model_config_dict={"temperature": 0},
    )
    
    return {
        'web_model': web_model,
        'document_processing_model': document_processing_model,
        'reasoning_model': reasoning_model,
        'image_analysis_model': image_analysis_model,
        'audio_reasoning_model': audio_reasoning_model,
        'web_agent_model': web_agent_model,
        'planning_agent_model': planning_agent_model
    }


# 全局模型实例（可以共享）
_models = create_models()


# ===== 工厂函数：为有状态的工具创建独立实例 =====

def create_browser_toolkit():
    r"""Create a new browser toolkit instance for concurrent safety.
    
    Each call creates an independent instance to avoid state conflicts
    during concurrent execution.

    Returns:
        AsyncBrowserToolkit: A new browser toolkit instance with unique cache directory.
    """
    session_id = str(uuid.uuid4())
    return AsyncBrowserToolkit(
        headless=True, 
        cache_dir=f"tmp/browser_{session_id}", 
        planning_agent_model=_models['planning_agent_model'], 
        web_agent_model=_models['web_agent_model']
    )


def create_code_execution_toolkit():
    r"""Create a new code execution toolkit instance for concurrent safety.
    
    Uses subprocess sandbox to avoid state sharing problems between
    concurrent executions.

    Returns:
        CodeExecutionToolkit: A new code execution toolkit instance.
    """
    return CodeExecutionToolkit(
        sandbox="subprocess",  # 使用subprocess确保并发安全
        verbose=True
    )


def create_document_processing_toolkit():
    r"""Create a new document processing toolkit instance for concurrent safety.
    
    Each call creates an independent instance to avoid cache directory
    conflicts during concurrent execution.

    Returns:
        DocumentProcessingToolkit: A new document processing toolkit instance with unique cache directory.
    """
    session_id = str(uuid.uuid4())
    return DocumentProcessingToolkit(cache_dir=f"tmp/doc_{session_id}")


def create_audio_analysis_toolkit():
    r"""Create a new audio analysis toolkit instance for concurrent safety.
    
    Each call creates an independent instance to avoid cache directory
    conflicts during concurrent execution.

    Returns:
        AudioAnalysisToolkit: A new audio analysis toolkit instance with unique cache directory.
    """
    session_id = str(uuid.uuid4())
    return AudioAnalysisToolkit(
        cache_dir=f"tmp/audio_{session_id}",
        audio_reasoning_model=_models['audio_reasoning_model']
    )


def create_video_analysis_toolkit():
    r"""Create a new video analysis toolkit instance for concurrent safety.
    
    Each call creates an independent instance to avoid download directory
    conflicts during concurrent execution.

    Returns:
        VideoAnalysisToolkit: A new video analysis toolkit instance with unique download directory.
    """
    session_id = str(uuid.uuid4())
    return VideoAnalysisToolkit(download_directory=f"tmp/video_{session_id}")


# ===== 并发安全的包装函数 =====

@dependencies_required("playwright")
async def browse_url(task_prompt: str, start_url: str) -> str:
    r"""A powerful toolkit which can simulate the browser interaction to solve the task which needs multi-step actions.

    Args:
        task_prompt (str): The task prompt to solve.
        start_url (str): The start URL to visit.

    Returns:
        str: The simulation result to the task.
    """
    # 创建新的browser toolkit实例
    browser_toolkit = create_browser_toolkit()
    
    try:
        # 调用browse_url方法
        result = await browser_toolkit.browse_url(task_prompt, start_url)
        return result
    except Exception as e:
        logger.error(f"Browse URL failed: {e}")
        raise
    finally:
        # 清理缓存（可选）
        try:
            browser_toolkit.browser.clean_cache()
        except Exception:
            pass


def execute_code(code: str, timeout: Optional[float] = None) -> str:
    r"""Execute a given code snippet

    Args:
        code (str): The input code to the Code Interpreter tool call.

    Returns:
        str: The text output from the Code Interpreter tool call.
    """
    # 创建新的code execution toolkit实例
    code_toolkit = create_code_execution_toolkit()
    
    try:
        # 调用execute_code方法
        result = code_toolkit.execute_code(code)
        return result
    except Exception as e:
        logger.error(f"Code execution failed: {e}")
        raise


def _post_process_html_content(html_content: str) -> str:
    r"""Post-process HTML content to extract meaningful text and reduce length.
    
    Args:
        html_content (str): Raw HTML content or markdown text from firecrawl/jina.
    
    Returns:
        str: Cleaned and summarized text content.
    """
    try:
        # 检测内容类型：如果包含大量markdown链接格式，说明是firecrawl/jina的输出
        markdown_link_count = len(re.findall(r'\[.*?\]\(.*?\)', html_content))
        is_markdown = markdown_link_count > 10
        
        if is_markdown:
            # 处理markdown格式的内容
            lines = html_content.split('\n')
            filtered_lines = []
            in_nav_section = False
            consecutive_links = 0
            
            # 定义导航关键词
            nav_keywords = [
                'home', 'login', 'register', 'download', 'share', 'comment', 'back',
                'product news', 'company news', 'android', 'chrome', 'play', 'platforms',
                'devices', 'fitbit', 'pixel', 'maps', 'news', 'search', 'shopping',
                'classroom', 'photos', 'translate', 'workspace', 'cloud', 'explore',
                'connect', 'communicate', 'see all', 'skip to', 'menu', 'navigation',
                'chromebooks', 'nest', 'registry', 'gemini', 'wear os', 'google play',
                'outreach', 'initiatives', 'technology', 'developers', 'health',
                'deepmind', 'sustainability', 'education', 'entrepreneurs', 'policy',
                'arts', 'culture', 'twitter', 'facebook', 'linkedin', 'mail', 'copy link'
            ]
            
            for line in lines:
                line_stripped = line.strip()
                
                # 跳过空行
                if not line_stripped:
                    continue
                
                # 跳过分隔线
                if re.match(r'^[=\-*_]{3,}$', line_stripped):
                    continue
                
                # 检测是否是markdown链接行
                is_link_line = bool(re.match(r'^\*?\s*\[.*?\]\(.*?\)\s*$', line_stripped))
                
                # 检测是否包含导航关键词
                line_lower = line_stripped.lower()
                has_nav_keyword = any(keyword in line_lower for keyword in nav_keywords)
                
                # 检测连续的链接（可能是导航菜单）
                if is_link_line:
                    consecutive_links += 1
                else:
                    consecutive_links = 0
                
                # 过滤条件
                should_skip = False
                
                # 1. 连续3个以上链接，认为是导航区域
                if consecutive_links >= 3:
                    should_skip = True
                
                # 2. 单独的链接行且包含导航关键词
                if is_link_line and has_nav_keyword:
                    should_skip = True
                
                # 3. 太短的行（<15字符）
                if len(line_stripped) < 15:
                    should_skip = True
                
                # 4. 只包含链接的列表项
                if re.match(r'^\*\s*\[.*?\]\(.*?\)\s*$', line_stripped):
                    should_skip = True
                
                # 5. 包含导航关键词且没有标点符号的短行
                if has_nav_keyword and len(line_stripped) < 50:
                    has_punctuation = any(p in line_stripped for p in ['。', '，', '！', '？', '.', ',', '!', '?', ':', ';'])
                    if not has_punctuation:
                        should_skip = True
                
                # 6. Published/Updated等元数据
                if re.match(r'^(published|updated|posted|by|author|tags|categories|related|share|copy)', line_lower):
                    should_skip = True
                
                if not should_skip:
                    # 移除markdown链接，只保留文本
                    cleaned_line = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', line_stripped)
                    if len(cleaned_line) >= 15:
                        filtered_lines.append(cleaned_line)
            
            text = '\n\n'.join(filtered_lines)
        
        else:
            # 处理HTML格式的内容（原有逻辑）
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # 移除所有无用标签
            for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside', 
                            'iframe', 'noscript', 'button', 'form', 'input', 'select',
                            'textarea', 'label', 'svg', 'path', 'img', 'link', 'meta']):
                tag.decompose()
            
            # 移除常见的导航和菜单区域
            for class_pattern in ['nav', 'menu', 'sidebar', 'footer', 'header', 
                                  'advertisement', 'ad-', 'social', 'share', 'comment',
                                  'related', 'recommend', 'toolbar', 'breadcrumb', 'widget']:
                for element in soup.find_all(class_=lambda x: x and class_pattern in str(x).lower()):
                    element.decompose()
            
            for id_pattern in ['nav', 'menu', 'sidebar', 'footer', 'header', 'ad']:
                for element in soup.find_all(id=lambda x: x and id_pattern in str(x).lower()):
                    element.decompose()
            
            # 移除包含大量链接的列表
            for ul in soup.find_all(['ul', 'ol']):
                links = ul.find_all('a')
                items = ul.find_all('li')
                if items and len(links) / len(items) > 0.8:
                    ul.decompose()
            
            # 提取主要内容区域
            main_content = None
            content_selectors = [
                'article', 'main', '[role="main"]',
                '.article-content', '.post-content', '.entry-content',
                '.main-content', '.content-body', '.article-body',
                '#article', '#content', '.detail-content', '.post-body'
            ]
            
            for selector in content_selectors:
                main_content = soup.select_one(selector)
                if main_content:
                    break
            
            if not main_content:
                candidates = soup.find_all(['div', 'section', 'article'])
                max_p_count = 0
                for candidate in candidates:
                    p_count = len(candidate.find_all('p'))
                    if p_count > max_p_count and p_count >= 3:
                        max_p_count = p_count
                        main_content = candidate
            
            if not main_content:
                main_content = soup.body if soup.body else soup
            
            # 提取文本
            paragraphs = []
            for element in main_content.find_all(['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
                parent_a = element.find_parent('a')
                if parent_a:
                    continue
                
                text = element.get_text(strip=True)
                if text and len(text) > 20:
                    paragraphs.append(text)
            
            if len(paragraphs) < 3:
                text = main_content.get_text(separator='\n', strip=True)
            else:
                text = '\n\n'.join(paragraphs)
        
        # 最后清理
        text = re.sub(r'\n\s*\n+', '\n\n', text)
        text = re.sub(r' +', ' ', text)
        text = text.strip()
        
        print("original length: ", len(html_content))
        print("after post-processing length: ", len(text))
        
        return text
    
    except Exception as e:
        logger.warning(f"HTML post-processing failed, returning truncated raw content: {e}")
        return html_content


def extract_document_content(document_path: str, query: str = None) -> Tuple[bool, str]:
    r"""Extract the content of a given local document and return the processed text. It can process various types of documents, including text, image, table, audio, video, zip, json, xml, pdf, py etc.

    Args:
        document_path (str): The local path of the document to be processed.
        query (str): The query to be used for retrieving the content. If the content is too long, the query will be used to identify which part contains the relevant information (like RAG). The query should be consistent with the current task.

    Returns:
        Tuple[bool, str]: A tuple containing a boolean indicating whether the document was processed successfully, and the content of the document (if success).
    """
    # 创建新的document processing toolkit实例
    doc_toolkit = create_document_processing_toolkit()
    
    try:
        # 调用extract_document_content方法
        result = doc_toolkit.extract_document_content(document_path, query)
        return result
    except Exception as e:
        logger.error(f"Document processing failed: {e}")
        raise
    finally:
        # 清理缓存
        try:
            doc_toolkit.clean_cache()
        except Exception:
            pass


# def extract_url_content(url: str, query: str = None) -> Tuple[bool, str]:
#     r"""Extract the html content of a given url and return the processed text.

#     Args:
#         url (str): The url of the webpage to be processed.
#         query (str): The query to be used for retrieving the content. If the content is too long, the query will be used to identify which part contains the relevant information (like RAG). The query should be consistent with the current task.

#     Returns:
#         Tuple[bool, str]: A tuple containing a boolean indicating whether the document was processed successfully, and the content of the document (if success).
#     """
#     # 创建新的document processing toolkit实例
#     doc_toolkit = create_document_processing_toolkit()
    
#     try:
#         # 调用extract_url_content方法
#         success, raw_content = doc_toolkit.extract_url_content(url, query)
        
#         if not success:
#             return success, raw_content
        
#         # 后处理：清理和精简HTML内容
#         processed_content = _post_process_html_content(raw_content)
        
#         return True, processed_content
#     except Exception as e:
#         logger.error(f"URL content extraction failed: {e}")
#         raise
#     finally:
#         # 清理缓存
#         try:
#             doc_toolkit.clean_cache()
#         except Exception:
#             pass


def ask_question_about_audio(audio_path: str, question: str) -> str:
    r"""Ask any question about the audio and get the answer using
        multimodal model.

    Args:
        audio_path (str): The path to the audio file.
        question (str): The question to ask about the audio.

    Returns:
        str: The answer to the question.
    """
    # 创建新的audio analysis toolkit实例
    audio_toolkit = create_audio_analysis_toolkit()
    
    try:
        # 调用ask_question_about_audio方法
        result = audio_toolkit.ask_question_about_audio(audio_path, question)
        return result
    except Exception as e:
        logger.error(f"Audio analysis failed: {e}")
        raise
    finally:
        # 清理缓存
        try:
            audio_toolkit.clean_cache()
        except Exception:
            pass


def ask_question_about_video(video_path: str, question: str) -> str:
    """
    Ask a question about the video using Gemini multimodal capabilities.
    
    Args:
        video_path (str): The path to the video file.
        question (str): The question to ask about the video.

    Returns:
        str: The answer to the question.
    """
    # 创建新的video analysis toolkit实例
    video_toolkit = create_video_analysis_toolkit()
    
    try:
        # 调用ask_question_about_video方法
        result = video_toolkit.ask_question_about_video(video_path, question)
        return result
    except Exception as e:
        logger.error(f"Video analysis failed: {e}")
        raise
    finally:
        # 清理下载目录
        try:
            video_toolkit.video_downloader_toolkit.clean_cache()
        except Exception:
            pass


def download_media_from_url(url: str, dest: Optional[str] = None) -> str:
    """
    Download any given URL (image, video, audio, document, or webpage).
    - If it's a direct link (file), download with requests.
    - If it's a webpage and yt-dlp is available, try yt-dlp.
    - Otherwise, save the HTML page.

    Args:
        url: Target URL
        dest: Output directory (default: current folder)

    Returns:
        str: Path of the downloaded file or directory.
    """
    # 如果没有指定目标目录，创建一个唯一的临时目录
    if dest is None:
        session_id = str(uuid.uuid4())
        dest = f"tmp/download_{session_id}"
        os.makedirs(dest, exist_ok=True)
    
    try:
        # 调用原始的download_media_from_url函数
        result = original_download_media_from_url(url, dest)
        return result
    except Exception as e:
        logger.error(f"Media download failed: {e}")
        raise


# ===== 带有重试机制的搜索函数包装器 =====

def search_tavily(query: str, topic: str = "general", time_range: str = "week",
                   include_raw_content: bool = False,
                   max_results: int = 5) -> List[Dict[str, Any]]:
    """Use Tavily AI search engine to search information for the given query. Tavily is optimized for AI agents and provides high-quality, relevant results with optional AI-generated answer summaries and full webpage content.

    Args:
        query (str): The query to be searched.
        topic (str): The search topic category (default: 'general'). Supported values:
            - 'general': General web search for broad topics
            - 'news': News-focused search, best for current events and recent developments
            - 'finance': Finance-focused search, best for stock, market, and economic data
        time_range (str): Time range filter (default: 'week'). Supported values:
            - 'day': Past 24 hours
            - 'week': Past week
            - 'month': Past month
            - 'year': Past year
            - None: No time filter
        include_raw_content (bool): Whether to include the full raw content of each webpage (default: False). When True, returns the complete webpage text in markdown format, useful for deep analysis. WARNING: significantly increases response size.
        max_results (int): The maximum number of results to return (default: 5, max: 20).

    Returns:
        List[Dict[str, Any]]: A list of dictionaries where each dictionary represents a search result.
            Each dictionary contains the following keys:
            - 'result_id' (int): A number in order (0 = AI answer summary).
            - 'title' (str): The title of the result.
            - 'description' (str): A content snippet of the result.
            - 'url' (str): The URL of the result.
            - 'score' (float): Relevance score between 0 and 1.
            - 'raw_content' (str, optional): Full webpage content in markdown (only when include_raw_content=True).
    """
    TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

    if not TAVILY_API_KEY:
        error_msg = "TAVILY_API_KEY environment variable is not set"
        logger.error(error_msg)
        return [{"error": error_msg}]

    responses = []

    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=TAVILY_API_KEY)

        logger.info(f"Searching Tavily: query='{query}', topic={topic}, max_results={max_results}, time_range={time_range}, include_raw_content={include_raw_content}")

        search_params = {
            "query": query,
            "max_results": max_results,
            "search_depth": "advanced",
            "topic": topic,
        }
        if time_range:
            search_params["time_range"] = time_range
        if include_raw_content:
            search_params["include_raw_content"] = "markdown"

        result = client.search(**search_params)

        # 如果有AI生成的答案摘要，添加到第一条结果
        if result.get("answer"):
            responses.append({
                "result_id": 0,
                "title": "AI Answer Summary",
                "description": result["answer"],
                "url": "",
                "score": 1.0,
            })

        if "results" in result:
            for i, item in enumerate(result["results"], start=1):
                response = {
                    "result_id": i,
                    "title": item.get("title", "N/A"),
                    "description": item.get("content", "N/A"),
                    "url": item.get("url", "N/A"),
                    "score": item.get("score", 0.0),
                }
                if include_raw_content and item.get("raw_content"):
                    response["raw_content"] = item["raw_content"]
                responses.append(response)

            logger.info(f"Tavily returned {len(result['results'])} results (response_time: {result.get('response_time', 'N/A')}s)")
        else:
            logger.warning("No results found in Tavily response")
            responses.append({"error": "No results found"})

    except Exception as e:
        error_msg = f"Tavily search failed: {str(e)}"
        logger.error(error_msg)
        responses.append({"error": error_msg})

    return responses


def search_volc(query: str, search_type: str = "web", count: int = 5,
                need_summary: bool = False) -> List[Dict[str, Any]]:
    """Use Volcengine (火山引擎) web search API to search information for the given query. This is the RECOMMENDED search tool for Chinese language queries, as it provides superior Chinese content coverage, built-in webpage full-text extraction, and structured data cards (weather, stocks, etc.).

    Args:
        query (str): The query to be searched (1~100 characters, Chinese or English).
        search_type (str): The type of search (default: 'web'). Supported values:
            - 'web': Standard web search, returns webpage results with full-text content
            - 'web_summary': Web search + AI-generated summary of results (uses streaming, slower but provides synthesized answer)
            - 'image': Image search, returns image results with URLs and dimensions
        count (int): The number of results to return (default: 5).
        need_summary (bool): Whether to generate AI summary (default: False). Only effective when search_type='web_summary', must be True in that case.

    Returns:
        List[Dict[str, Any]]: A list of dictionaries where each dictionary represents a search result.
            Each dictionary contains the following keys:
            - 'result_id' (int): A number in order (0 = AI summary if search_type='web_summary').
            - 'title' (str): The title of the result.
            - 'description' (str): A content snippet of the result.
            - 'url' (str): The URL of the result.
            - 'score' (float): Relevance score between 0 and 1.
            - 'content' (str): Full webpage text content (for web/web_summary search).
            - 'publish_time' (str): Publish time in ISO 8601 format.
            - 'site_name' (str): Name of the source website.
    """
    VOLC_SEARCH_API_KEY = os.getenv("VOLC_SEARCH_API_KEY")

    if not VOLC_SEARCH_API_KEY:
        error_msg = "VOLC_SEARCH_API_KEY environment variable is not set"
        logger.error(error_msg)
        return [{"error": error_msg}]

    responses = []
    api_url = "https://open.feedcoopapi.com/search_api/web_search"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {VOLC_SEARCH_API_KEY}",
    }

    try:
        import requests as _requests

        payload = {
            "Query": query,
            "SearchType": search_type,
            "Count": count,
        }
        if search_type == "web_summary":
            payload["NeedSummary"] = True

        logger.info(f"Searching Volcengine: query='{query}', type={search_type}, count={count}")

        if search_type == "web_summary":
            # 流式 SSE 返回
            resp = _requests.post(api_url, headers=headers, json=payload, timeout=60, stream=True)
            resp.raise_for_status()

            search_results = []
            summary_parts = []

            for raw_line in resp.iter_lines():
                if not raw_line:
                    continue
                line = raw_line.decode("utf-8")
                if not line.startswith("data:"):
                    continue
                try:
                    data = json.loads(line[5:])
                    result = data.get("Result", {})
                    if result.get("WebResults") and not search_results:
                        search_results = result["WebResults"]
                    for choice in (result.get("Choices") or []):
                        delta = choice.get("Delta", {})
                        if delta and delta.get("Content"):
                            summary_parts.append(delta["Content"])
                except json.JSONDecodeError:
                    continue

            # AI 总结作为第一条
            summary = "".join(summary_parts)
            if summary:
                responses.append({
                    "result_id": 0,
                    "title": "AI Summary",
                    "description": summary,
                    "url": "",
                    "score": 1.0,
                    "content": summary,
                    "publish_time": "",
                    "site_name": "Volcengine AI",
                })

            for item in search_results:
                responses.append({
                    "result_id": item.get("SortId", 0),
                    "title": item.get("Title", ""),
                    "description": item.get("Snippet", ""),
                    "url": item.get("Url", ""),
                    "score": item.get("RankScore", 0.0),
                    "content": item.get("Content", ""),
                    "publish_time": item.get("PublishTime", ""),
                    "site_name": item.get("SiteName", ""),
                })

            logger.info(f"Volcengine web_summary returned {len(search_results)} results + AI summary ({len(summary)} chars)")

        else:
            # 普通 web / image 搜索
            resp = _requests.post(api_url, headers=headers, json=payload, timeout=30)
            resp.raise_for_status()
            data = resp.json()

            if data.get("ResponseMetadata", {}).get("Error"):
                err = data["ResponseMetadata"]["Error"]
                error_msg = f"Volcengine search error: {err.get('Code')} - {err.get('Message')}"
                logger.error(error_msg)
                return [{"error": error_msg}]

            result = data.get("Result", {})

            if search_type == "image":
                for item in (result.get("ImageResults") or []):
                    img = item.get("Image", {})
                    responses.append({
                        "result_id": item.get("SortId", 0),
                        "title": item.get("Title", ""),
                        "description": f"Image {img.get('Width')}x{img.get('Height')} ({img.get('Shape', '')})",
                        "url": img.get("Url", ""),
                        "score": item.get("RankScore", 0.0),
                        "content": "",
                        "publish_time": item.get("PublishTime", ""),
                        "site_name": item.get("SiteName", ""),
                    })
            else:
                for item in (result.get("WebResults") or []):
                    responses.append({
                        "result_id": item.get("SortId", 0),
                        "title": item.get("Title", ""),
                        "description": item.get("Snippet", ""),
                        "url": item.get("Url", ""),
                        "score": item.get("RankScore", 0.0),
                        "content": item.get("Content", ""),
                        "publish_time": item.get("PublishTime", ""),
                        "site_name": item.get("SiteName", ""),
                    })

            logger.info(f"Volcengine {search_type} returned {result.get('ResultCount', 0)} results (TimeCost: {result.get('TimeCost')}ms)")

    except Exception as e:
        error_msg = f"Volcengine search failed: {str(e)}"
        logger.error(error_msg)
        responses.append({"error": error_msg})

    return responses


def search_arxiv(query: str, max_results: int = 5,
                 search_field: str = "all",
                 sort_by: str = "relevance") -> List[Dict[str, Any]]:
    """Search academic papers on arXiv. Best for preprints in computer science, physics, mathematics, etc.

    Args:
        query (str): The search query (English recommended).
        max_results (int): Maximum number of results to return (default: 5, max: 50).
        search_field (str): Which field to search in (default: 'all'). Supported values:
            - 'all': Full text search (title + abstract + full text)
            - 'ti': Title only (use for known paper titles)
            - 'au': Author name
            - 'abs': Abstract only
            - 'cat': Category (e.g. 'cs.AI', 'cs.CL')
        sort_by (str): Sort order (default: 'relevance'). Supported values:
            - 'relevance': Most relevant first
            - 'submittedDate': Most recent first
            - 'lastUpdatedDate': Recently updated first

    Returns:
        List[Dict[str, Any]]: A list of dictionaries, each representing a paper:
            - 'result_id' (int): Index starting from 1.
            - 'title' (str): Paper title.
            - 'authors' (list[str]): Author names.
            - 'abstract' (str): Paper abstract.
            - 'url' (str): arXiv abstract page URL.
            - 'pdf_url' (str): Direct PDF URL.
            - 'year' (int): Publication year.
            - 'published' (str): Publication date (ISO 8601).
            - 'categories' (list[str]): arXiv category tags.
            - 'doi' (str): DOI if available.
    """
    import urllib.parse
    import urllib.request
    from xml.etree import ElementTree as ET

    ARXIV_NS = {
        "atom": "http://www.w3.org/2005/Atom",
        "arxiv": "http://arxiv.org/schemas/atom",
    }

    responses = []
    try:
        # 构建查询字符串
        if search_field in ("ti", "au", "abs", "cat"):
            search_query = f'{search_field}:"{query}"' if search_field == "ti" else f"{search_field}:{query}"
        else:
            search_query = f"all:{query}"

        params = urllib.parse.urlencode({
            "search_query": search_query,
            "start": 0,
            "max_results": min(max_results, 50),
            "sortBy": sort_by,
            "sortOrder": "descending",
        })
        url = f"http://export.arxiv.org/api/query?{params}"
        logger.info(f"Searching arXiv: query='{query}', field={search_field}, max_results={max_results}")

        with urllib.request.urlopen(url, timeout=30) as resp:
            xml_text = resp.read().decode("utf-8")

        root = ET.fromstring(xml_text)
        idx = 0
        for entry in root.findall("atom:entry", ARXIV_NS):
            # title
            title_el = entry.find("atom:title", ARXIV_NS)
            title = title_el.text.strip().replace("\n", " ") if title_el is not None else ""
            if not title:
                continue

            # abstract
            summary_el = entry.find("atom:summary", ARXIV_NS)
            abstract = summary_el.text.strip().replace("\n", " ") if summary_el is not None else ""

            # authors
            authors = []
            for author_el in entry.findall("atom:author", ARXIV_NS):
                name_el = author_el.find("atom:name", ARXIV_NS)
                if name_el is not None:
                    authors.append(name_el.text.strip())

            # year & published
            year = None
            published_str = ""
            published_el = entry.find("atom:published", ARXIV_NS)
            if published_el is not None:
                published_str = published_el.text.strip()
                import re as _re
                m = _re.search(r"(\d{4})", published_str)
                if m:
                    year = int(m.group(1))

            # links
            entry_url = None
            pdf_url = None
            for link_el in entry.findall("atom:link", ARXIV_NS):
                href = link_el.get("href", "")
                if link_el.get("rel") == "alternate":
                    entry_url = href
                elif link_el.get("title") == "pdf":
                    pdf_url = href

            id_el = entry.find("atom:id", ARXIV_NS)
            if id_el is not None and not entry_url:
                entry_url = id_el.text.strip()
            if not pdf_url and entry_url:
                pdf_url = entry_url.replace("/abs/", "/pdf/") + ".pdf"

            # categories
            categories = []
            for cat_el in entry.findall("atom:category", ARXIV_NS):
                term = cat_el.get("term")
                if term:
                    categories.append(term)

            # doi
            doi_el = entry.find("arxiv:doi", ARXIV_NS)
            doi = doi_el.text.strip() if doi_el is not None else ""

            idx += 1
            responses.append({
                "result_id": idx,
                "title": title,
                "authors": authors,
                "abstract": abstract,
                "url": entry_url or "",
                "pdf_url": pdf_url or "",
                "year": year,
                "published": published_str,
                "categories": categories,
                "doi": doi,
            })

        logger.info(f"arXiv returned {len(responses)} results")

    except Exception as e:
        error_msg = f"arXiv search failed: {str(e)}"
        logger.error(error_msg)
        responses.append({"error": error_msg})

    return responses


def search_openalex(query: str, max_results: int = 5,
                    search_mode: str = "fulltext",
                    year_from: int = None,
                    sort: str = "relevance",
                    open_access_only: bool = False) -> List[Dict[str, Any]]:
    """Search academic papers on OpenAlex. Covers 250M+ works with citation counts, open access info, and author details. Good for broad academic search across all disciplines.

    Args:
        query (str): The search query.
        max_results (int): Maximum number of results to return (default: 5, max: 50).
        search_mode (str): How to search (default: 'fulltext'). Supported values:
            - 'fulltext': Search across title and abstract (broad)
            - 'title': Search title only (precise)
        year_from (int): Only return papers published from this year onwards (e.g. 2023). None means no filter.
        sort (str): Sort order (default: 'relevance'). Supported values:
            - 'relevance': Most relevant first
            - 'cited_by_count': Most cited first
            - 'publication_date': Most recent first
        open_access_only (bool): If True, only return open access papers (default: False).

    Returns:
        List[Dict[str, Any]]: A list of dictionaries, each representing a paper:
            - 'result_id' (int): Index starting from 1.
            - 'title' (str): Paper title.
            - 'authors' (list[str]): Author names.
            - 'abstract' (str): Paper abstract (reconstructed from inverted index).
            - 'url' (str): DOI URL or OpenAlex URL.
            - 'pdf_url' (str): Open access PDF URL (if available).
            - 'year' (int): Publication year.
            - 'cited_by_count' (int): Number of citations.
            - 'journal' (str): Journal/source name.
            - 'type' (str): Work type (article, review, preprint, etc.).
            - 'doi' (str): DOI string.
    """
    import requests as _requests

    OPENALEX_URL = "https://api.openalex.org/works"
    responses = []

    try:
        params = {
            "per_page": min(max_results, 50),
            "mailto": os.getenv("OPENALEX_EMAIL", "user@example.com"),
        }

        # 搜索模式
        if search_mode == "title":
            params["filter"] = f'title.search:"{query}"'
        else:
            params["search"] = query

        # 过滤条件
        filters = []
        if year_from:
            filters.append(f"from_publication_date:{year_from}-01-01")
        if open_access_only:
            filters.append("open_access.is_oa:true")
        if filters:
            existing = params.get("filter", "")
            params["filter"] = ",".join([existing] + filters) if existing else ",".join(filters)

        # 排序
        sort_map = {
            "relevance": "relevance_score:desc",
            "cited_by_count": "cited_by_count:desc",
            "publication_date": "publication_date:desc",
        }
        if sort in sort_map:
            params["sort"] = sort_map[sort]

        logger.info(f"Searching OpenAlex: query='{query}', mode={search_mode}, max_results={max_results}")

        resp = _requests.get(OPENALEX_URL, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        for i, work in enumerate(data.get("results", []), start=1):
            # authors
            authors = []
            for authorship in work.get("authorships", []):
                name = authorship.get("author", {}).get("display_name", "")
                if name:
                    authors.append(name)

            # abstract: reconstruct from inverted index
            abstract = ""
            inv_idx = work.get("abstract_inverted_index")
            if inv_idx:
                word_positions = []
                for word, positions in inv_idx.items():
                    for pos in positions:
                        word_positions.append((pos, word))
                word_positions.sort()
                abstract = " ".join(w for _, w in word_positions)

            # URLs
            doi = work.get("doi", "") or ""
            doi_short = doi.replace("https://doi.org/", "") if doi else ""
            url = doi or work.get("id", "")

            # PDF
            pdf_url = ""
            oa = work.get("open_access", {})
            oa_url = oa.get("oa_url", "")
            best_loc = work.get("best_oa_location") or {}
            pdf_url = best_loc.get("pdf_url", "") or oa_url or ""

            # journal
            primary_loc = work.get("primary_location") or {}
            source = primary_loc.get("source") or {}
            journal = source.get("display_name", "")

            responses.append({
                "result_id": i,
                "title": work.get("title", ""),
                "authors": authors,
                "abstract": abstract,
                "url": url,
                "pdf_url": pdf_url,
                "year": work.get("publication_year"),
                "cited_by_count": work.get("cited_by_count", 0),
                "journal": journal,
                "type": work.get("type", ""),
                "doi": doi_short,
            })

        logger.info(f"OpenAlex returned {len(responses)} results (total matches: {data.get('meta', {}).get('count', 'N/A')})")

    except Exception as e:
        error_msg = f"OpenAlex search failed: {str(e)}"
        logger.error(error_msg)
        responses.append({"error": error_msg})

    return responses


def search_crossref(query: str, max_results: int = 5,
                    search_mode: str = "fulltext",
                    year_from: int = None,
                    sort: str = "relevance",
                    article_only: bool = False) -> List[Dict[str, Any]]:
    """Search academic papers on CrossRef, the largest DOI registry with 150M+ records. Best for finding published journal articles with DOI, citation counts, and full metadata.

    Args:
        query (str): The search query.
        max_results (int): Maximum number of results to return (default: 5, max: 50).
        search_mode (str): How to search (default: 'fulltext'). Supported values:
            - 'fulltext': Search across all bibliographic fields
            - 'title': Title-only search
            - 'author': Author-name search
        year_from (int): Only return papers published from this year onwards (e.g. 2023). None means no filter.
        sort (str): Sort order (default: 'relevance'). Supported values:
            - 'relevance': Most relevant first
            - 'cited': Most cited first (is-referenced-by-count)
            - 'published': Most recent first
        article_only (bool): If True, only return journal articles (default: False).

    Returns:
        List[Dict[str, Any]]: A list of dictionaries, each representing a paper:
            - 'result_id' (int): Index starting from 1.
            - 'title' (str): Paper title.
            - 'authors' (list[str]): Author names.
            - 'abstract' (str): Paper abstract (may contain HTML tags).
            - 'url' (str): DOI URL.
            - 'year' (int): Publication year.
            - 'cited_by_count' (int): Number of citations.
            - 'journal' (str): Journal name.
            - 'type' (str): Work type (journal-article, proceedings-article, etc.).
            - 'doi' (str): DOI string.
    """
    import requests as _requests

    CROSSREF_URL = "https://api.crossref.org/works"
    MAILTO = os.getenv("CROSSREF_EMAIL", "user@example.com")
    responses = []

    try:
        params = {
            "rows": min(max_results, 50),
            "mailto": MAILTO,
        }

        # 搜索模式
        if search_mode == "title":
            params["query.title"] = query
        elif search_mode == "author":
            params["query.author"] = query
        else:
            params["query"] = query

        # 过滤条件
        filters = []
        if year_from:
            filters.append(f"from-pub-date:{year_from}-01-01")
        if article_only:
            filters.append("type:journal-article")
        if filters:
            params["filter"] = ",".join(filters)

        # 排序
        sort_map = {
            "relevance": ("relevance", "desc"),
            "cited": ("is-referenced-by-count", "desc"),
            "published": ("published", "desc"),
        }
        if sort in sort_map:
            params["sort"] = sort_map[sort][0]
            params["order"] = sort_map[sort][1]

        headers = {
            "User-Agent": f"InternResearchBot/1.0 (mailto:{MAILTO})",
        }

        logger.info(f"Searching CrossRef: query='{query}', mode={search_mode}, max_results={max_results}")

        resp = _requests.get(CROSSREF_URL, params=params, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        msg = data.get("message", {})

        for i, item in enumerate(msg.get("items", []), start=1):
            # title
            title_list = item.get("title", [])
            title = title_list[0] if title_list else ""

            # authors
            authors = []
            for a in item.get("author", []):
                given = a.get("given", "")
                family = a.get("family", "")
                if given and family:
                    authors.append(f"{given} {family}")
                elif family:
                    authors.append(family)

            # year
            year = None
            pub_date = item.get("published", {}).get("date-parts", [[]])[0]
            if pub_date and len(pub_date) > 0:
                year = pub_date[0]

            # journal
            journal_list = item.get("container-title", [])
            journal = journal_list[0] if journal_list else ""

            # abstract: strip HTML tags
            abstract = item.get("abstract", "")
            if abstract:
                import re as _re
                abstract = _re.sub(r"<[^>]+>", "", abstract).strip()

            responses.append({
                "result_id": i,
                "title": title,
                "authors": authors,
                "abstract": abstract,
                "url": item.get("URL", ""),
                "year": year,
                "cited_by_count": item.get("is-referenced-by-count", 0),
                "journal": journal,
                "type": item.get("type", ""),
                "doi": item.get("DOI", ""),
            })

        logger.info(f"CrossRef returned {len(responses)} results (total matches: {msg.get('total-results', 'N/A')})")

    except Exception as e:
        error_msg = f"CrossRef search failed: {str(e)}"
        logger.error(error_msg)
        responses.append({"error": error_msg})

    return responses


def search_google(query: str, num_result_pages: int = 10, time_range: str = "d3",
                  region: str = "None", language: str = "zh-cn") -> List[Dict[str, Any]]:
    """Use Google search engine via Serper API to search information for the given query.
    
    Args:
        query (str): The query to be searched.
        num_result_pages (int): The number of result pages to retrieve (default: 10).
        time_range (str): Time range filter. The default value is 'd3', which means the past 3 days. Supported values:
            - 'h' or 'd1': Past 24 hours (1 day)
            - 'd3': Past 3 days
            - 'w' or 'd7': Past week (7 days)
            - 'm' or 'm1': Past month
            - 'y' or 'y1': Past year
        region (str): Optional region/country code (ISO 3166-1 alpha-2). Common examples:
            - 'cn': China (中国，推荐用于中文搜索)
            - 'us': United States
            - 'jp': Japan
            - 'uk': United Kingdom
        language (str): Search interface language. Supported values:
            - 'zh-cn': Simplified Chinese (简体中文，默认)
            - 'en': English
    
    Returns:
        List[Dict[str, Any]]: A list of dictionaries where each dictionary represents a website.
            Each dictionary contains the following keys:
            - 'result_id': A number in order.
            - 'title': The title of the website.
            - 'description': A brief description of the website.
            - 'long_description': More detail of the website.
            - 'url': The URL of the website.
            
            Example:
            {
                'result_id': 1,
                'title': 'OpenAI',
                'description': 'An organization focused on ensuring that artificial general intelligence benefits all of humanity.',
                'long_description': 'OpenAI is a non-profit artificial intelligence research company...',
                'url': 'https://www.openai.com'
            }
    """
    SERPER_API_KEY = os.getenv("SERPER_API_KEY")
    
    if not SERPER_API_KEY:
        error_msg = "SERPER_API_KEY environment variable is not set"
        logger.error(error_msg)
        return [{"error": error_msg}]
    
    responses = []
    
    try:
        url = "https://google.serper.dev/search"
        headers = {
            "X-API-KEY": SERPER_API_KEY,
            "Content-Type": "application/json",
        }
        
        video_sites = [
            "youtube.com",
            "youtu.be",
            "bilibili.com",
            "video.tv",
            "vimeo.com",
            "dailymotion.com",
            "tiktok.com"
        ]
        exclude_clause = " ".join([f"-site:{site}" for site in video_sites])
        enhanced_query = f"{query} {exclude_clause}"
        
        payload = {
            "q": enhanced_query,
            "num": num_result_pages
        }
        
        if time_range:
            time_mapping = {
                'h': 'qdr:h',
                'd1': 'qdr:d',
                'd3': 'qdr:d3',
                'w': 'qdr:w',
                'd7': 'qdr:w',
                'm': 'qdr:m',
                'm1': 'qdr:m',
                'y': 'qdr:y',
                'y1': 'qdr:y',
            }
            
            tbs_value = time_mapping.get(time_range.lower())
            if tbs_value:
                payload["tbs"] = tbs_value
            else:
                logger.warning(f"Unknown time_range value '{time_range}', ignoring time filter")
        
        if region and region.lower() != "none":
            payload["gl"] = region.lower()
        
        if not language:
            language = "zh-cn"
        payload["hl"] = language.lower()
        
        logger.info(f"Searching Google via Serper API: query='{query}', num_results={num_result_pages}, time_range={time_range}, region={region}, language={language}")
        
        import requests as _requests
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
        
        session = _requests.Session()
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        
        result = session.post(url, headers=headers, json=payload, timeout=30, verify=True)
        result.raise_for_status()
        data = result.json()
        
        if "organic" in data:
            search_items = data["organic"]
            logger.info(f"Found {len(search_items)} organic search results from API")
            
            for i, search_item in enumerate(search_items, start=1):
                title = search_item.get("title", "N/A")
                snippet = search_item.get("snippet", "N/A")
                link = search_item.get("link", "N/A")
                
                response = {
                    "result_id": i,
                    "title": title,
                    "description": snippet,
                    "url": link,
                }
                
                responses.append(response)
        else:
            error_msg = "No organic results found in Serper API response"
            logger.warning(error_msg)
            responses.append({"error": error_msg})
    
    except Exception as e:
        error_msg = f"Google search via Serper API failed: {str(e)}"
        logger.error(error_msg)
        responses.append({"error": error_msg})
    
    return responses


def search_wiki(entity: str) -> str:
    r"""Search the entity in WikiPedia and return the summary of the
            required page, containing factual information about
            the given entity.

        Args:
            entity (str): The entity to be searched.

        Returns:
            str: The search result. If the page corresponding to the entity
                exists, return the summary of this entity in a string.
        """
    search_toolkit = SearchToolkit()
    max_retries = 3
    
    for attempt in range(max_retries):
        try:
            result = search_toolkit.search_wiki(entity)
            return result
        except Exception as e:
            logger.warning(f"Wiki search attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)  # 指数退避：1, 2, 4 秒
            else:
                logger.error(f"Wiki search failed after {max_retries} attempts")
                raise


def search_archived_webpage(url: str, date: str) -> Tuple[bool, str]:
    r"""Given a url, search the wayback machine and returns the archived version of the url for a given date.

        Args:
            url (str): The url to search for.
            date (str): The date to search for. The format should be YYYYMMDD.
        Returns:
            Tuple[bool, str]: A tuple containing a boolean indicating whether the archived version was found and the information to be returned.
        """
    search_toolkit = SearchToolkit()
    max_retries = 3
    
    for attempt in range(max_retries):
        try:
            result = search_toolkit.search_archived_webpage(url, date)
            return result
        except Exception as e:
            logger.warning(f"Archived webpage search attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)  # 指数退避：1, 2, 4 秒
            else:
                logger.error(f"Archived webpage search failed after {max_retries} attempts")
                raise

def extract_and_answer_query_from_url(url: str, query: str) -> Tuple[bool, str]:
    r"""Extract the content of a given url and answer the query according to the content.

    Uses Tavily extract API for fast content extraction, with Volcengine as fallback.
    Then sends the extracted content + query to LLM for answering.

    Args:
        url (str): The url of the webpage to be processed.
        query (str): The question to answer.

    Returns:
        Tuple[bool, str]: A tuple containing a boolean indicating whether the tool was executed successfully, and the answer to the query according to the content of the url (if success).
    """
    # Step 1: Extract content from URL using Tavily extract (fast) or Volc fallback
    content = None

    # 尝试 Tavily extract
    TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
    if TAVILY_API_KEY:
        try:
            from tavily import TavilyClient
            client = TavilyClient(api_key=TAVILY_API_KEY)
            logger.info(f"Extracting URL content via Tavily: {url}")
            result = client.extract(urls=[url], extract_depth="advanced", format="markdown")
            results = result.get("results", [])
            if results and results[0].get("raw_content"):
                content = results[0]["raw_content"]
                logger.info(f"Tavily extract success: {len(content)} chars")
        except Exception as e:
            logger.warning(f"Tavily extract failed: {e}")

    # Tavily 失败时用 Volc 搜索 URL 获取内容
    if not content:
        VOLC_KEY = os.getenv("VOLC_SEARCH_API_KEY")
        if VOLC_KEY:
            try:
                import requests as _requests
                logger.info(f"Extracting URL content via Volc: {url}")
                resp = _requests.post(
                    "https://open.feedcoopapi.com/search_api/web_search",
                    headers={"Content-Type": "application/json", "Authorization": f"Bearer {VOLC_KEY}"},
                    json={"Query": url, "SearchType": "web", "Count": 1},
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()
                web_results = data.get("Result", {}).get("WebResults", [])
                if web_results:
                    content = web_results[0].get("Content", "") or web_results[0].get("Snippet", "")
                    if content:
                        logger.info(f"Volc extract success: {len(content)} chars")
            except Exception as e:
                logger.warning(f"Volc extract failed: {e}")

    # 两种方式都失败，回退到原始方法
    if not content:
        logger.warning("Tavily and Volc extract both failed, falling back to original method")
        try:
            from tools.info_processing_tools import extract_url_content
            content = extract_url_content(url, timeout=120)
        except Exception as e:
            return False, f"All extraction methods failed for {url}: {e}"

    if not content:
        return False, f"Failed to extract content from {url}"

    # Step 2: Truncate if too long (LLM context limit)
    max_content_len = 64000
    if len(content) > max_content_len:
        content = content[:max_content_len] + f"\n\n[... truncated, total {len(content)} chars]"

    # Step 3: Ask LLM to answer query based on content
    try:
        from models import get_model
        # Local patch: honor EXTRACTION_MODEL instead of a hardcoded 'gpt-4o-mini',
        # matching the convention in info_processing_tools.py.
        model = get_model(os.getenv("EXTRACTION_MODEL", "gpt-4o-mini"))

        prompt = f"""Based on the following content extracted from a webpage, answer the question comprehensively.

Question: {query}

Webpage Content:
{content}

Please provide a detailed and accurate answer based on the content above. If the content does not contain enough information to answer the question, state what information is available and what is missing."""

        answer = model.generate(prompt, auto_fix_json=False, temperature=0.3, max_tokens=4000)
        return True, answer

    except Exception as e:
        logger.error(f"LLM answer generation failed: {e}")
        return False, f"Content extracted ({len(content)} chars) but LLM answer failed: {e}"


def construct_agent_list(config: Dict[str, Any] = None) -> List[Dict[str, Any]]:
    """
    构建工具列表，支持根据配置过滤工具
    
    Args:
        config: 配置字典，包含enabled_tools列表
        
    Returns:
        过滤后的工具列表
    """

    # 创建无状态或状态安全的工具包实例（这些可以共享）
    image_analysis_toolkit = ImageAnalysisToolkit(model=_models['image_analysis_model'])
    excel_toolkit = ExcelToolkit()

    # 定义所有可用工具及其映射关系
    all_tools = [
        ("web_search", FunctionTool(search_google)),                 # 带重试机制的Google搜索
        ("tavily_search", FunctionTool(search_tavily)),                   # Tavily AI搜索引擎
        ("volc_search", FunctionTool(search_volc)),                       # 火山引擎联网搜索（中文搜索推荐）
        ("arxiv_search", FunctionTool(search_arxiv)),                     # arXiv论文搜索
        ("openalex_search", FunctionTool(search_openalex)),               # OpenAlex学术搜索（2.5亿+论文）
        ("crossref_search", FunctionTool(search_crossref)),               # CrossRef DOI搜索（1.5亿+记录）
        ("search_wiki", FunctionTool(search_wiki)),                   # 带重试机制的Wikipedia搜索
        ("web_search", FunctionTool(search_archived_webpage)),   # 带重试机制的存档网页搜索
        ("file_processor", FunctionTool(extract_document_content)),  # 并发安全的文档处理
        ("url_processor", FunctionTool(extract_and_answer_query_from_url)),      # 并发安全的URL内容提取
        ("paper_processor", FunctionTool(summarize_paper)), 
        ("video_processor", FunctionTool(ask_question_about_video)), # 并发安全的视频分析
        ("image_processor", FunctionTool(image_analysis_toolkit.ask_question_about_image)),  # 安全：仅持有模型
        ("audio_processor", FunctionTool(ask_question_about_audio)), # 并发安全的音频分析
        ("code_executor", FunctionTool(execute_code)),             # 并发安全的代码执行
        ("media_downloader", FunctionTool(download_media_from_url)),  # 并发安全的媒体下载
        ("ocr", FunctionTool(ocr2text)),            # OCR文字识别
        ("browser_use", FunctionTool(browse_url)),       # 浏览器
        ("literature_search", FunctionTool(search_academic_papers)),        # 多源学术论文搜索
        ("report_generation", FunctionTool(search_and_summarize_papers)),        # 多源学术论文搜索
        ("report_generation", FunctionTool(search_and_summarize_webpages)),  # 集成搜索、提取、分析功能
         

    ]
    
    # 如果没有配置，返回所有工具    
    if config is None:
        return [tool for _, tool in all_tools]
    
    # 获取启用的工具列表
    enabled_tools = config.get('tools', {}).get('enabled_tools', [])
    
    # 如果没有指定enabled_tools，返回所有工具
    if not enabled_tools:
        return [tool for _, tool in all_tools]
    
    # 过滤工具
    filtered_tools = []
    for tool_category, tool in all_tools:
        if tool_category in enabled_tools:
            filtered_tools.append(tool)
    
    logger.info(f"根据配置过滤工具: 启用工具类别 {enabled_tools}, 过滤后工具列表: {filtered_tools}, 过滤后工具数量: {len(filtered_tools)}")
    
    return filtered_tools


if __name__ == "__main__":
    # Test the tools
    tools = construct_agent_list()
    print(f"Successfully created {len(tools)} tools:")
    for i, tool in enumerate(tools):
        print(f"{i+1}. {tool.func.__name__}")

    # _, result = extract_document_content("/path/to/local/pdf")
    # print(len(result))

    # result = search_google("AI科学家的最新研究")
    # print(result)
    # print(len(result))


    # _, result = extract_url_content("https://www.secrss.com/articles/75214")
    # print(result)
    # print(len(result))

    res = search_wiki("Yangtze River Flood")
    print("res: ", res)

