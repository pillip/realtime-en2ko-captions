# Business Analysis: Real-Time Multilingual Captioning Service

**Date**: 2026-03-19
**Updated**: 2026-04-29
**Analyst Confidence**: Medium-High (제품 실전 검증 완료, 시장 데이터 다수 확보, 한국 특화 TAM은 추정치)

---

## Executive Summary

행사 에이전시 운영자가 직접 개발한 실시간 다국어 자막 시스템. OpenAI Realtime API(음성 인식) + AWS Bedrock Claude(LLM 번역)으로 통역사 대비 1/3 가격에 동등 품질 제공.

**Recommendation: Go** -- 제품 검증 완료(국외 행사 3건 실전 투입), 보안/테스트 정비 완료(311 tests, 85% coverage), 95% 마진 구조. 1차 타겟은 행사 에이전시/학회 대상 관리형 서비스, 이후 셀프서비스 SaaS로 전환.

**2026-04-29 업데이트**: 제품 성숙도 대폭 향상 (24개 이슈 shipped, 보안 강화, 4개 언어 지원, 멀티 룸/뷰어 설계 완료). 매출 전략을 "에이전시 내부 도구"에서 "외부 판매 포함 하이브리드"로 확장.

---

## Market Analysis

### Global Market Context

| Segment | 2025 Value | Projected Value | CAGR | Source |
|---------|-----------|----------------|------|--------|
| Live Captioning Market | $2.4B | $6.47B (2033) | 13.2% | [Global Growth Insights](https://www.globalgrowthinsights.com/market-reports/live-captioning-market-106098) |
| AI Speech-to-Text Tools | $3.30B | $16.42B (2035) | 17.41% | [Precedence Research](https://www.precedenceresearch.com/ai-speech-to-text-tool-market) |
| AI Video Translation | $2.68B (2024) | $33.4B (2034) | 28.7% | [Market.us](https://market.us/report/ai-video-translation-market/) |
| Language Translation Software | $68.04B | $116.55B (2035) | 5.53% | [Precedence Research](https://www.precedenceresearch.com/language-translation-software-market) |
| South Korea AI Translation Services | $26.74M | $129.83M (2035) | 17.12% | [Market Research Future](https://www.marketresearchfuture.com/reports/south-korea-ai-enabled-translation-services-market-58676) |

### TAM / SAM / SOM Estimates

**TAM**: ~$2.4B globally for live captioning (2025).

**SAM**: Rough estimate ~$50-100M. AI-powered real-time translation for conferences/events with Korean language needs.

**SOM**: Rough estimate ~$500K-2M in Year 1. 행사 에이전시/학회 타겟, 건당 80-350만원.

**Data gap**: "Korean-language conference captioning"은 독립 시장 데이터가 존재하지 않음. SAM/SOM은 인접 시장 데이터 기반 추정치.

### Key Market Trends

1. **Platform-native translation 확대**: Zoom 46+ 언어, Teams 50+ 언어 번역 자막 내장. 기본 번역을 commodity화.
2. **AI가 일상 행사의 통역사를 대체 중**: 70-90% 비용 절감. 고위험 행사(법률, 외교)만 인간 통역 유지.
3. **하이브리드 이벤트 성장**: 오프라인 + 온라인 동시 자막 수요 증가.
4. **SaaS 가격 모델 변화**: 38% SaaS 기업이 사용량 기반 요금제(UBP) 채택, 2026년 61% 예상.

---

## Competitive Landscape

### Direct Competitors

| Competitor | Type | Languages | Pricing | Korean Quality | Key Strength | Key Weakness |
|-----------|------|-----------|---------|---------------|-------------|-------------|
| **Wordly.ai** | AI-only platform | 60+ | From $75/hr | Generic AI | Purpose-built for events | No specialized Korean quality |
| **KUDO** | Hybrid (AI + human) | 100+ | Enterprise custom | High (human) | Human quality option | Expensive, complex setup |
| **Interprefy** | Human-first + AI | 200+ | Custom enterprise | High (human) | Professional interpretation | Cost, requires scheduling |
| **Zoom Translated Captions** | Built-in feature | 46+ | Included in plans | Supported | Zero friction for Zoom users | Locked to Zoom, generic quality |
| **MS Teams Live Captions** | Built-in feature | 50+ | Teams Premium | Supported | Enterprise integration | Locked to Teams |
| **Akkadu** | AI captions | 90+ | Custom (usage-based) | Supported | Privacy-focused | No public pricing |
| **JotMe** | Zoom plugin | 45+ | $9/mo | Supported | Cheap, Zoom integration | Meeting-focused, not events |

### Differentiation Assessment (Updated 2026-04-29)

**강점:**
- **한국어 번역 품질**: LLM(Claude) 의역 vs 경쟁사 NMT 직역. 존댓말, 맥락, 뉘앙스 처리에서 실질적 차이.
- **가격 경쟁력**: Wordly $75/hr vs 이 서비스 5-7만원/hr. 통역사 200만원/반일 vs 80만원/반일.
- **멀티 룸 지원** (구현 예정): 대형 컨퍼런스 멀티 트랙 동시 운영 가능.
- **뷰어 QR 접속** (구현 예정): 청중 개인 디바이스에서 원하는 언어로 자막 확인.
- **Self-hosted 가능**: 데이터 민감 조직(정부, 의료)에 적합.

**약점 (기존 대비 개선된 항목):**
- ~~보안 취약점~~ → **해소** (HMAC 쿠키, DB 인증 검증, CSPRNG 토큰)
- ~~단일 언어 쌍~~ → **해소** (한/영/중/베 4개 언어 지원)
- ~~테스트 부족~~ → **해소** (311 tests, 85% coverage, CI/CD)

**남은 약점:**
- Zoom/Teams 네이티브 연동 없음
- 1인 개발/운영 (확장성 한계)
- Streamlit UI 한계 (엔터프라이즈급 UX 아님)

---

## Business Model (Updated 2026-04-29)

### Target Customers

| 타겟 | 구매 동기 | 구매 패턴 | 우선순위 |
|------|----------|----------|----------|
| **행사 에이전시** | 통역사 대체, 원가 절감 | 건당 구매, 반복적 | **1차 타겟** |
| **학회/협회** | 제한 예산 내 다국어 자막 | 건당 구매, 연 1-2회 | **1차 타겟** |
| **기업 행사팀** | 사내 컨퍼런스 자체 운영 | 연간 계약 | 2차 타겟 |
| **소규모 이벤트 기획자** | 저렴한 자막 솔루션 | 셀프서비스 | 3차 타겟 (셀프서비스 전환 후) |

### Pricing Strategy: Event Package Model

| 패키지 | 내용 | 가격 | API 원가 | 마진 |
|--------|------|------|----------|------|
| **Half Day** | 4시간, 1룸, 2언어 | 80만원 | ~4만원 | 95% |
| **Full Day** | 8시간, 1룸, 2언어 | 150만원 | ~8만원 | 95% |
| **Conference** | 8시간, 3룸, 4언어 | 350만원 | ~30만원 | 91% |
| **추가 언어** | 언어당 | +20만원/일 | ~5만원 | 75% |

**경쟁 비교:**
- 인간 통역사: 반일 200만원 (2언어면 400만원+)
- Wordly: $75/hr = 시간당 ~10만원
- 이 서비스: 반일 80만원 (통역사 대비 60% 저렴, Wordly 대비 30-50% 저렴)

### Cost Basis (Per Minute)

| Cost Component | Per Minute | Source |
|---------------|-----------|--------|
| OpenAI Realtime API (audio input) | ~$0.06 | [OpenAI Pricing](https://openai.com/api/pricing/) |
| OpenAI Realtime API (text output) | ~$0.01 | [OpenAI Pricing](https://openai.com/api/pricing/) |
| AWS Bedrock Claude translation | ~$0.01-0.03 | [AWS Bedrock Pricing](https://aws.amazon.com/bedrock/pricing/) |
| AWS infrastructure | ~$0.005 | Rough estimate |
| **Total COGS per minute** | **~$0.08-0.10** | |

### Operating Model: Hybrid (관리형 → 셀프서비스)

**Phase 1 (현재)**: 관리형
- 고객 문의 → 룸 세팅 → 운영 가이드/원격 지원
- 학회/협회 등 기술 이해도 낮은 고객에 적합
- 고객 패턴을 학습하여 셀프서비스 설계 재료 확보

**Phase 2 (6-12개월 후)**: 하이브리드
- 행사 에이전시 → 셀프서비스 (기술 이해도 높음)
- 학회/협회 → 관리형 유지 또는 프리미엄 지원
- 셀프서비스 가입/결제/룸 생성 자동화

---

## Revenue Projections

### 단계별 매출 목표

| 단계 | 내용 | 필요 기능 | 매출 효과 |
|------|------|----------|----------|
| **Now** | 에이전시 내부 사용 확대 | 현재 기능 | 연 600만→1,500만원 |
| **Phase 1** | 멀티 룸 + 뷰어 완성 | ISSUE-25~33 | 대형 행사/멀티 트랙 수주 |
| **Phase 2** | 외부 에이전시/학회 관리형 판매 | 결제, 온보딩 | 건당 80-350만원 |
| **Phase 3** | 셀프서비스 SaaS | 자동 가입/결제 | 월 반복 매출(MRR) |
| **Future** | Zoom/Meet 플러그인, STT 전용 | 플러그인 개발 | 시장 확장 |

### 연간 매출 시나리오 (Phase 2 기준)

| 지표 | 보수적 | 낙관적 |
|------|--------|--------|
| 외부 고객 수 | 5개사 | 15개사 |
| 건당 평균 단가 | 100만원 | 200만원 |
| 연간 이벤트 수 | 15건 | 40건 |
| **외부 매출** | **1,500만원** | **8,000만원** |
| 내부(에이전시) 매출 | 1,500만원 | 3,000만원 |
| **총 매출** | **3,000만원** | **1.1억원** |
| API 원가 | ~150만원 | ~500만원 |
| **마진율** | **95%** | **95%** |

---

## SWOT Analysis (Updated 2026-04-29)

### Strengths

1. **실전 검증 완료**: 국외 행사 3건 실전 투입, 간접 매출 발생 중
2. **한국어 번역 품질**: LLM 의역이 NMT 직역 대비 존댓말/맥락 처리에서 우위
3. **압도적 마진 구조**: 95% 마진 (API 원가 3-4만원, 판매가 80-150만원)
4. **운영자 = 고객**: 에이전시 직접 운영으로 도메인 지식, 유통, 영업 문제 해소
5. **제품 성숙도**: 311 tests, 85% coverage, 보안 강화, CI/CD, 4언어 지원
6. **Self-hosted 가능**: 데이터 민감 조직 대응 가능

### Weaknesses

1. **Zoom/Teams 연동 없음**: 별도 브라우저 탭 필요 (UX 마찰)
2. **1인 개발/운영**: 동시 관리형 서비스 제공에 물리적 한계
3. **Streamlit UI 한계**: 엔터프라이즈급 UX로의 전환 필요
4. **셀프서비스 미비**: 결제, 자동 가입, 온보딩 등 SaaS 인프라 없음

### Opportunities

1. **행사 에이전시 시장**: 같은 업종 네트워크를 통한 자연스러운 영업
2. **학회 국제 행사**: 예산 제한적이지만 다국어 자막 수요 높음
3. **멀티 룸 + 뷰어 QR**: 경쟁사 대비 차별화 기능 (청중 개인 디바이스 자막)
4. **API 비용 하락 추세**: OpenAI/AWS 가격 지속 하락 → 마진 개선
5. **STT 전용 모드**: 번역 없이 자막만 → 국내 행사/청각 접근성 시장 확장
6. **Zoom/Meet 플러그인**: 플랫폼 마켓플레이스 유통 (JotMe $9/mo 사례)

### Threats

1. **Zoom/Teams 내장 자막 품질 향상**: 2-3년 내 "good enough" 수준 도달 가능성
2. **경쟁사의 한국어 모델 강화**: Wordly/KUDO가 한국어 특화 시 품질 격차 축소
3. **OpenAI API 가격 변동**: 2-3배 인상 시 원가 구조 변동
4. **1인 운영의 확장 한계**: 관리형 고객 10건 이상 시 병목
5. **한국 시장 크기 천장**: SAM ~$50-100M으로 대규모 성장에 한계

---

## Risks & Mitigations (Updated)

| # | Risk | Severity | Likelihood | Mitigation |
|---|------|----------|-----------|------------|
| 1 | **OpenAI API 가격 인상/중단** | High | Medium | STT 추상화 레이어 구축, Whisper/Deepgram 대안 평가 |
| 2 | **Zoom/Teams 내장 자막 품질 "good enough" 도달** | High | High | 전문 용어집, 셀프 호스팅, 멀티 룸/뷰어 QR 등 플랫폼 내장 불가 기능으로 차별화 |
| 3 | **관리형 서비스 확장 병목** | Medium | High | 10건 이상 운영 시 셀프서비스 전환 가속, 파트타임 운영 인력 확보 |
| 4 | **AWS Bedrock 번역 비용 급등** | Medium | Low | AWS Translate(NMT) 기본 + Claude "프리미엄" 계층 분리 |
| 5 | **한국 시장 크기 한계** | Medium | Medium | 일본어-영어, 중국어-영어 등 아시아 언어 쌍 확장 |
| 6 | **Streamlit 확장성 한계** | Medium | Medium | 동시 접속 50명 초과 시 FastAPI + React 전환 계획 |
| 7 | ~~보안 취약점~~ | ~~High~~ | ~~해소~~ | HMAC 쿠키, DB 인증, CSPRNG 토큰 구현 완료 |

---

## Success Probability Assessment (2026-04-29)

### 유리한 점

1. **이미 돈을 받고 있다** — 3건이지만 실제 고객이 비용을 지불한 검증된 제품
2. **본인이 고객이다** — 에이전시 운영 경험으로 니즈를 추측이 아닌 경험으로 파악
3. **마진 구조가 압도적** — 인건비 기반 경쟁사(통역사)는 이 마진을 이길 수 없음
4. **타이밍** — AI 번역이 "실전 투입 가능" 수준을 막 넘긴 시점

### 불리한 점

1. **시장 크기가 작다** — 한국어 컨퍼런스 자막은 니치, 10억 사업은 구조적으로 어려움
2. **Zoom/Teams 품질이 계속 좋아진다** — 2-3년 후 품질 차이 유지 불확실
3. **1인 운영 한계** — 관리형 서비스는 시간을 파는 것, 동시 다수 고객 지원 어려움
4. **셀프서비스 전환 비용** — 온보딩, 결제, 문서화, CS 전부 구축 필요

### 시나리오별 성공 확률

| 시나리오 | 확률 | 조건 |
|----------|------|------|
| 에이전시 부가서비스로 연 3,000만원 | **70-80%** | 멀티 룸 완성, 현재 영업 궤도 유지 |
| 외부 판매 포함 연 1억원 | **30-40%** | 외부 고객 10건+ 확보, 관리형 운영 리소스 |
| SaaS로 연 3억원+ | **10-15%** | 셀프서비스 전환, 마케팅, 경쟁 돌파 |

### 추천 전략: 부트스트랩 SaaS

1. 에이전시 매출로 생존 확보
2. 외부 관리형 고객을 하나씩 늘림 (목표: 10건+)
3. **관리형으로 10건 이상 운영하면서 "고객이 어디서 막히는지" 패턴 수집**
4. 그 패턴이 셀프서비스 설계의 재료
5. 급하게 SaaS 전환하지 않고, 관리형에서 검증된 것만 자동화

---

## Product Roadmap Alignment

### 현재 이슈 현황 (2026-04-29)

**완료 (24 issues shipped)**:
- 언어 선택 UI + WebSocket 프로토콜 + 번역 확장 + 자동 감지 (ISSUE-1~4)
- 보안 강화: WebSocket DB 인증, CSPRNG 토큰, HMAC 쿠키 (ISSUE-5,9,10)
- 테스트/품질: 311 tests, 85% coverage, CI/CD (ISSUE-6~8,14,17,18,22)
- 기술 부채: 데드 코드 제거, FK/인덱스/WAL, 의존성 정리 (ISSUE-11~13,15,16,19~21,23,24)

**다음 (9 issues, 미구현)**:
- 멀티 룸: ISSUE-25~29 (5d) — 대형 행사/외부 판매의 전제 조건
- 뷰어: ISSUE-30~33 (4d) — 청중 QR 접속, 차별화 기능

### 매출 기여도 기준 우선순위

| 순위 | 기능 | 매출 영향 | 이슈 |
|------|------|----------|------|
| 1 | 멀티 룸 | 대형 행사 수주 가능, Conference 패키지(350만원) 판매 근거 | ISSUE-25~29 |
| 2 | 뷰어 QR | 셀링 포인트 ("청중 개인 디바이스에서 원하는 언어로") | ISSUE-30~33 |
| 3 | 결제/온보딩 | 외부 관리형 판매 시 필수 | 미생성 |
| 4 | STT 전용 모드 | 국내 시장 확장, 저가 상품 라인 | 미생성 |
| 5 | Zoom/Meet 플러그인 | 플랫폼 유통, MRR 모델 | 미생성 |

---

## Appendix: Key Sources

- [Global Growth Insights - Live Captioning Market](https://www.globalgrowthinsights.com/market-reports/live-captioning-market-106098)
- [Precedence Research - AI Speech to Text Tool Market](https://www.precedenceresearch.com/ai-speech-to-text-tool-market)
- [Market.us - AI Video Translation Market](https://market.us/report/ai-video-translation-market/)
- [Precedence Research - Language Translation Software Market](https://www.precedenceresearch.com/language-translation-software-market)
- [Market Research Future - South Korea AI Translation Services](https://www.marketresearchfuture.com/reports/south-korea-ai-enabled-translation-services-market-58676)
- [Wordly.ai Pricing](https://www.wordly.ai/pricing)
- [Akkadu AI Captions](https://akkadu.ai/)
- [OpenAI API Pricing](https://openai.com/api/pricing/)
- [AWS Bedrock Pricing](https://aws.amazon.com/bedrock/pricing/)
- [Zoom Translated Captions](https://support.zoom.com/hc/en/article?id=zm_kb&sysparm_article=KB0060844)
- [Zoom Marketplace - Transcription & Translation](https://marketplace.zoom.us/apps?category=transcription-translation)
- [JotMe Zoom Translation](https://www.jotme.io/zoom/translation)
- [Thunderbit - 2026 SaaS Market Trends](https://thunderbit.com/blog/saas-statistics-market-trends)
- [Growth Unhinged - SaaS Pricing 2025](https://www.growthunhinged.com/p/2025-state-of-saas-pricing-changes)
