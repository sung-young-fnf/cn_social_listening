릴스(Reels) 크롤링 가능 여부 확인 결과

API (이미지와 동일)
GET /api/v1/media/{media_id}/info/
→ 이미지 게시물과 완전히 동일한 API를 사용. media_type이 2, product_type이 "clips"이면 릴스.

릴스 전용 추가 필드 (이미지에 없는 것들)
필드 | 값 (실제 확인) | 설명
media_type | 2 | 비디오 (이미지는 1, 캐러셀은 8)
product_type | "clips" | 릴스 식별자
play_count | 618,604 | ✅조회수 (릴스에서만 존재)
ig_play_count | 618,604 | 인스타그램 내 조회수
video_duration | 초 단위 영상 길이 | 영상 재생 시간
video_versions | 3가지 해상도 | 동영상 URL (720x1280 등)
image_versions2 | 썸네일 URL | 릴스 커버 이미지
clips_metadata | 릴스 전용 메타 | 음악, 챌린지 등

이미지 vs 릴스 비교
필드 | 이미지 게시물 | 릴스
imageUrl | ✅ image_versions2.candidates[0].url | ✅ 썸네일로 사용 가능
videoUrl | ❌ 없음 | ✅ video_versions[0].url
thumbnailUrl | ✅ | ✅ image_versions2.candidates[0].url
engagement.views | ❌ 이미지 해당 없음 | ✅ play_count
engagement.likes | ✅ like_count | ✅ like_count
engagement.comments | ✅ comment_count | ✅ comment_count
engagement.shares | ⚠️ media_repost_count | ⚠️ media_repost_count
engagement.saves | ❌ | ❌
metadata.publishedAt | ✅ taken_at | ✅ taken_at
author 정보 전체 | ✅ | ✅

요청 필드 기준 릴스 전체 정리
요청 필드 | 가능 여부 | 비고
id | ✅ | pk
videoUrl | ✅ | video_versions[0].url (CDN URL)
thumbnailUrl | ✅ | image_versions2.candidates[0].url
s3VideoUrl | ❌ | Instagram CDN URL만 제공
s3ThumbnailUrl | ❌ | 동일author.*✅이미지와 동일
content.url | ✅ | https://instagram.com/reels/{code}/
content.text | ✅ | caption.text
engagement.likes | ✅ | like_count
engagement.comments | ✅ | comment_count
engagement.views | ✅ | play_count (릴스에만 있음!)
engagement.shares | ⚠️ | media_repost_count
engagement.saves | ❌ | 공개 API 미지원
metadata.publishedAt | ✅ | taken_at