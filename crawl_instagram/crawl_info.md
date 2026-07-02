Instagram 크롤링 가능 데이터 정리

API 1. 유저 정보
GET /api/v1/users/{user_id}/info/

필드 | 설명
pk | 유저ID
username | 유저네임
full_name | 표시 이름
is_verified | 인증 여부
follower_count | 팔로워 수
following_count | 팔로잉 수
media_count | 게시물 수
biography | 소개글
profile_pic_url | 프로필 이미지 
URLis_private | 비공개 여부

user_id는 프로필 페이지 HTML에서 "user_id":"숫자" 패턴으로 추출


API 2. 게시물 상세 정보
GET /api/v1/media/{media_id}/info/

필드 | 설명
pk / id | 게시물 ID
taken_at | 게시 시각 (unix timestamp)
media_type | 미디어 타입 (1=이미지, 8=캐러셀)
like_count | 좋아요 수
comment_count | 댓글 수
media_repost_count | 리포스트(공유) 수
image_versions2.candidates[0].url | 이미지 원본 URL
carousel_media[].image_versions2 | 캐러셀 각 이미지 URL
caption.text | 본문 텍스트 (해시태그 포함)
code | shortcode (URL용, ex. DXoigNhIIuH)
location | 위치 정보
user.pk | 작성자 유저 ID
user.username | 작성자 유저네임
user.full_name | 작성자 표시 이름
user.profile_pic_url | 작성자 프로필 이미지
user.is_verified | 작성자 인증 여부
carousel_media_count | 캐러셀 이미지 수

API 3. 댓글 목록
GET /api/v1/media/{media_id}/comments/

필드 | 설명
comments[].text | 댓글 내용
comments[].user.username | 댓글 작성자
comments[].created_at | 댓글 작성 시각
comments[].like_count | 댓글 좋아요 수
comment_count | 총 댓글 수

요청 필드 대비 가능 여부 요약
요청 필드 | 가능 여부 | API / 비고
id | ✅ | media/{id}/info/
imageUrl | ✅ | image_versions2.candidates[0].url
thumbnailUrl | ✅ | image_versions2.candidates 중 작은 해상도
author.id | ✅ | user.pk
author.username | ✅ | user.username
author.displayName | ✅ | user.full_name
author.verified | ✅ | user.is_verified
author.avatarUrl | ✅ | user.profile_pic_url
author.followers | ✅ | 별도로 users/{user_id}/info/ 호출 필요
author.profileUrl | ✅ | username 조합으로 생성 가능
content.url | ✅ | https://instagram.com/p/{code}/
content.text | ✅ | caption.text
content.contentType | ✅ | media_type 값으로 판단
metadata.hashtags | ✅ | caption.text에서 파싱
metadata.mentions | ✅ | caption.text에서 파싱
metadata.publishedAt | ✅ | taken_at (unix → ISO 변환)
metadata.fetchedAt | ✅ | 크롤링 시점에 직접 기록
metadata.language | ⚠️ | API 미제공, 별도 언어 감지 라이브러리 필요
platform | ✅ | 하드코딩 "instagram"
engagement.likes✅like_count
engagement.comments | ✅ | comment_count
engagement.shares | ⚠️ | media_repost_count (리포스트만, DM 공유 수는 불가)
engagement.saves | ❌ | 공개 API 미지원
engagement.views❌이미지 게시물엔 해당 없음

공통 요청 헤더
X-IG-App-ID: 936619743392459
X-Requested-With: XMLHttpRequest
Cookie: sessionid=... (로그인 세션 필요)

주의: 로그인 세션(sessionid 쿠키)이 있어야 API 응답이 옴. 대량 자동화 시 Instagram 이용약관 위반 및 계정 차단 위험 있음.