"""
번역 서비스 모듈
LLM 번역, 문장 분리, 언어 감지 기능 제공
"""

import json
import re

# 언어 이름 매핑 (표시용)
SOURCE_LANG_NAMES = {
    "en": "영어",
    "ko": "한국어",
    "ja": "일본어",
    "zh": "중국어",
    "vi": "베트남어",
    "es": "스페인어",
    "fr": "프랑스어",
    "de": "독일어",
}

# Bedrock 모델 ID (안정성 순서)
BEDROCK_MODEL_IDS = [
    "anthropic.claude-3-5-sonnet-20240620-v1:0",
    "anthropic.claude-3-haiku-20240307-v1:0",
    "anthropic.claude-3-sonnet-20240229-v1:0",
]


def detect_language(text, output_lang="ko"):
    """다국어 감지 (유니코드 범위 기반)

    감지 우선순위: 한국어 > 중국어 > 베트남어 > 영어(기본)
    detected == output_lang이면 source를 "en"으로 폴백.

    Args:
        text: 감지 대상 텍스트
        output_lang: 출력 언어 (detected와 같으면 폴백)

    Returns:
        (source_lang, target_lang) 튜플
    """
    # 1. Korean: hangul range
    has_korean = any(0xAC00 <= ord(c) <= 0xD7A3 for c in text)
    if has_korean:
        detected = "ko"
        if detected == output_lang:
            return "ko", "en"
        return "ko", output_lang

    # 2. Chinese: CJK unified ideographs
    has_chinese = any(0x4E00 <= ord(c) <= 0x9FFF for c in text)
    if has_chinese:
        detected = "zh"
        if detected == output_lang:
            return "zh", "en"
        return "zh", output_lang

    # 3. Vietnamese: special diacritics
    vietnamese_chars = set("ăơưđĂƠƯĐ")
    has_vietnamese = any(c in vietnamese_chars for c in text)
    if has_vietnamese:
        detected = "vi"
        if detected == output_lang:
            return "vi", "en"
        return "vi", output_lang

    # 4. Default: English
    detected = "en"
    if detected == output_lang:
        return "en", "ko"
    return "en", output_lang


def split_into_sentences(text, language="ko"):
    """텍스트를 문장 단위로 분리"""
    if language.startswith("ko"):
        # 한국어 문장 분리 개선
        # 1. 명확한 문장 종결 패턴
        text = re.sub(r"([.!?])([가-힣])", r"\1 \2", text)

        # 2. 다양한 종결 어미 고려
        pattern = (
            r"(?<=[.!?])"
            r"|(?<=다)(?=[\s])"
            r"|(?<=요)(?=[\s.!?])"
            r"|(?<=까)(?=[\s.!?])"
            r"|(?<=네)(?=[\s.!?])"
            r"|(?<=군)(?=[\s.!?])"
            r"|(?<=나)(?=[\s.!?])"
        )
        sentences = re.split(pattern, text)

        # 재결합 및 정리
        result = []
        current = ""
        for sent in sentences:
            current += sent
            if re.search(r"[.!?]$|[다요까네군나]$", current.strip()):
                if current.strip():
                    result.append(current.strip())
                current = ""
        if current.strip():
            result.append(current.strip())
        return result
    else:
        # 영어 등 문장 분리
        sentences = re.split(r"(?<=[.!?])\s+", text)
        return [s.strip() for s in sentences if s.strip()]


def _build_prompt_to_korean(text, source_lang):
    """한국어 번역 프롬프트 생성"""
    source_lang_name = SOURCE_LANG_NAMES.get(source_lang, "원본 언어")
    header = (
        f"다음 {source_lang_name} 텍스트를 "
        "청중이 듣기 좋은 자연스러운 한국어로 의역해주세요."
    )

    return f"""{header}
실시간 컨퍼런스/기술발표 자막으로 사용되며, 완전한 직역보다는 의미 전달이 우선입니다.

원문: "{text}"

번역 가이드라인:
- 💡 의미 중심: 원문의 핵심 의미를 자연스럽게 전달
- 🎯 청중 친화적: 듣는 사람이 이해하기 쉬운 한국어 표현
- 🚀 맥락 반영: 기술발표/비즈니스 상황에 맞는 톤앤매너
- ⚡ 간결성: 실시간 자막에 적합한 깔끔한 문장 (최대 2문장)
- 🔧 용어 처리: 기술용어는 한국 개발자들이 실제 사용하는 표현
- 📝 자연스러움: 한국어 어순과 관용표현 우선, 직역 금지

예시 변환:
- "Let me walk you through" → "함께 살펴보겠습니다"
- "It's pretty straightforward" → "사실 꽤 간단합니다"
- "This is game-changing" → "이건 정말 혁신적이에요"
- "That landed differently for me" → "제게는 다르게 다가왔습니다"

번역 결과만 출력하세요 (설명, 주석, 부연설명 일절 금지):

한국어 번역:"""


def _build_prompt_to_english(text, source_lang=None):
    """영어 번역 프롬프트 생성"""
    source_lang_name = (
        SOURCE_LANG_NAMES.get(source_lang, "원본 언어") if source_lang else "한국어"
    )
    header = (
        f"다음 {source_lang_name} 텍스트를 "
        "국제 컨퍼런스에서 쓰이는 자연스러운 영어로 의역해주세요."
    )
    return f"""{header}
글로벌 청중을 위한 실시간 자막으로,
직역보다는 의미가 잘 전달되는 것이 중요합니다.

원문: "{text}"

번역 가이드라인:
- 글로벌 표준: 국제 컨퍼런스에서 실제 쓰이는 자연스러운 영어
- 프로페셔널: 기술발표/비즈니스에 적합한 톤
- 명확성: 비영어권 청중도 이해하기 쉬운 표현
- 간결성: 자막에 적합한 깔끔한 문장
- 용어 활용: 업계 표준 기술용어 및 표현 사용

예시 변환:
- "이걸 한번 보시면" -> "Let's take a look at this"
- "꽤 괜찮은 것 같아요" -> "This looks pretty promising"
- "정말 대단한 기술이에요" -> "This is truly impressive technology"

번역 결과만 출력하세요 (설명, 주석, 부연설명 일절 금지):

English translation:"""


def _build_prompt_to_chinese(text, source_lang):
    """중국어 번역 프롬프트 생성"""
    source_lang_name = SOURCE_LANG_NAMES.get(source_lang, "原始语言")
    return f"""请将以下{source_lang_name}文本翻译成自然流畅的中文。
这是实时会议/技术演讲的字幕，意译优先于直译。

原文: "{text}"

翻译指南:
- 语义为主: 自然传达原文的核心含义
- 受众友好: 使用听众容易理解的中文表达
- 场景适配: 符合技术演讲/商务场景的语气
- 简洁明了: 适合实时字幕的简洁句子（最多2句）
- 术语处理: 技术术语使用中国开发者常用的表达
- 自然流畅: 优先使用中文语序和惯用表达，避免直译

请只输出翻译结果（禁止任何说明、注释、附加解释）:

中文翻译:"""


def _build_prompt_to_vietnamese(text, source_lang):
    """베트남어 번역 프롬프트 생성"""
    source_lang_name = SOURCE_LANG_NAMES.get(source_lang, "ngon ngu goc")
    header = (
        f"Hay dich doan van ban {source_lang_name} sau day sang tieng Viet tu nhien."
    )
    return f"""{header}
Day la phu de truc tiep cho hoi nghi/thuyet trinh ky thuat,
uu tien truyen dat y nghia hon la dich sat.

Van ban goc: "{text}"

Huong dan dich:
- Tap trung y nghia: Truyen dat tu nhien y chinh cua van ban goc
- Than thien voi nguoi nghe: Su dung cach dien dat tieng Viet de hieu
- Phu hop ngu canh: Giong dieu phu hop voi thuyet trinh ky thuat/kinh doanh
- Ngan gon: Cau ngan gon phu hop voi phu de truc tiep (toi da 2 cau)
- Xu ly thuat ngu: Su dung thuat ngu ky thuat pho bien tai Viet Nam
- Tu nhien: Uu tien trat tu tu va cach dien dat tieng Viet

Chi xuat ket qua dich (khong giai thich, khong chu thich, khong bo sung):

Ban dich tieng Viet:"""


def _clean_llm_response(translated_text):
    """LLM 응답에서 불필요한 텍스트 제거"""
    translated_text = translated_text.strip("\"'")

    # 설명 텍스트 제거
    translated_text = re.sub(
        r"This translation:.*$",
        "",
        translated_text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    translated_text = re.sub(
        r"Here\'s a natural.*?:",
        "",
        translated_text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    translated_text = re.sub(
        r"This.*?:", "", translated_text, flags=re.DOTALL | re.IGNORECASE
    )

    # 첫 번째 줄만 추출
    lines = translated_text.split("\n")
    if lines:
        translated_text = lines[0].strip()

    # 따옴표 제거
    translated_text = re.sub(r'^["\'](.+)["\']$', r"\1", translated_text)

    return translated_text.strip()


def _invoke_bedrock_with_fallback(bedrock_client, body):
    """여러 모델을 시도하며 Bedrock 호출"""
    for model_id in BEDROCK_MODEL_IDS:
        try:
            response = bedrock_client.invoke_model(
                modelId=model_id,
                body=body,
                contentType="application/json",
                accept="application/json",
            )
            return response
        except Exception as model_error:
            print(f"    ⚠️ {model_id} 모델 실패: {model_error}")
            if model_id == BEDROCK_MODEL_IDS[-1]:
                raise model_error
    return None


def translate_with_llm(bedrock_client, text, source_lang, target_lang):
    """Bedrock LLM을 사용한 고품질 컨텍스트 번역"""
    try:
        if target_lang == "ko":
            prompt = _build_prompt_to_korean(text, source_lang)
        elif target_lang == "zh":
            prompt = _build_prompt_to_chinese(text, source_lang)
        elif target_lang == "vi":
            prompt = _build_prompt_to_vietnamese(text, source_lang)
        else:
            prompt = _build_prompt_to_english(text, source_lang)

        body = json.dumps(
            {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 200,
                "messages": [
                    {"role": "user", "content": [{"type": "text", "text": prompt}]}
                ],
                "temperature": 0.5,
                "top_p": 0.9,
            }
        )

        response = _invoke_bedrock_with_fallback(bedrock_client, body)
        response_body = json.loads(response["body"].read())
        translated_text = response_body["content"][0]["text"].strip()

        return _clean_llm_response(translated_text)

    except Exception as e:
        print(f"    ❌ LLM 번역 실패: {e}")
        return None
