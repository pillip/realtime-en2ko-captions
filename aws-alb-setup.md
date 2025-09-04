# AWS ALB + ACM SSL 배포 가이드

## 1. Target Group 생성

### Console에서 설정:
1. **EC2 Console** → Target Groups → Create target group
2. **Basic Configuration**:
   - Target type: `Instances`
   - Target group name: `realtime-caption-tg`
   - Protocol: `HTTP`
   - Port: `8501`
   - VPC: Default VPC

3. **Health Check Settings**:
   - Health check protocol: `HTTP`
   - Health check path: `/`
   - Health check port: `Traffic port`
   - Healthy threshold: `2`
   - Unhealthy threshold: `2`
   - Timeout: `5`
   - Interval: `30`
   - Success codes: `200`

4. **Register Targets**:
   - EC2 인스턴스 선택
   - Port: `8501`
   - Include as pending below 클릭

## 2. Application Load Balancer 리스너 설정

### HTTP 리스너 (포트 80):
- Action: Redirect to HTTPS
- Port: 443
- Status code: HTTP 301

### HTTPS 리스너 (포트 443):
- Protocol: HTTPS
- Port: 443
- SSL certificate: **ACM에서 발급받은 인증서 선택**
- Default action: Forward to `realtime-caption-tg`

## 3. Route 53 도메인 연결

### A Record 생성:
1. **Route 53 Console** → Hosted zones → your-domain.com
2. **Create record**:
   - Record name: (비워두면 root domain)
   - Record type: `A`
   - Alias: `Yes`
   - Alias target: `Application Load Balancer`
   - Region: ALB가 있는 리전 선택
   - ALB 선택

## 4. EC2 보안 그룹 수정

### EC2 인스턴스 보안 그룹:
- **Inbound rules**:
  - HTTP (8501): ALB 보안 그룹에서만 허용
  - SSH (22): 본인 IP만 허용
- **ALB에서만 접근 허용하도록 제한**

## 5. Streamlit 설정 수정

### HTTPS 프록시 헤더 처리를 위한 설정:
