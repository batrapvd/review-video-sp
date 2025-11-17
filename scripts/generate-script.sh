#!/bin/bash

# Content generation script

set -e

if [ -z "$1" ]; then
    echo "Usage: $0 <video-data.json>"
    exit 1
fi

VIDEO_DATA_FILE="$1"

# Extract product info
PRODUCT_NAME=$(jq -r '.productInfo.name' "$VIDEO_DATA_FILE")
PRICE=$(jq -r '.productInfo.price' "$VIDEO_DATA_FILE")
ORIGINAL_PRICE=$(jq -r '.productInfo.originalPrice' "$VIDEO_DATA_FILE")
DISCOUNT=$(jq -r '.productInfo.discount' "$VIDEO_DATA_FILE")

# Convert price format: 269.000₫ -> 269k
PRICE_FORMATTED=$(echo "$PRICE" | sed 's/\.000₫/k/g' | sed 's/₫/k/g')
ORIGINAL_PRICE_FORMATTED=$(echo "$ORIGINAL_PRICE" | sed 's/\.000₫/k/g' | sed 's/₫/k/g')

echo "Product: $PRODUCT_NAME"
echo "Price: $PRICE -> $PRICE_FORMATTED"
echo "Original Price: $ORIGINAL_PRICE -> $ORIGINAL_PRICE_FORMATTED"
echo "Discount: $DISCOUNT"
echo "Video Duration: ${VIDEO_DURATION}s"
echo "Target Script Length: ${TARGET_SCRIPT_LENGTH} characters"

# Create prompt for AI
PROMPT="Hãy tạo 2 phần nội dung cho video TikTok/Reels về sản phẩm sau:

Tên sản phẩm: $PRODUCT_NAME
Giá hiện tại: $PRICE_FORMATTED
Giá gốc: $ORIGINAL_PRICE_FORMATTED
Giảm giá: $DISCOUNT

PHẦN 1 - SCRIPT VOICE-OVER:
Yêu cầu:
1. QUAN TRỌNG: Script phải có CHÍNH XÁC ${TARGET_SCRIPT_LENGTH} ký tự (bao gồm dấu câu và khoảng trắng) để khớp với video dài ${VIDEO_DURATION} giây. Đây là yêu cầu BẮT BUỘC để script nói liền mạch từ đầu đến cuối video.
2. Giọng điệu hấp dẫn, thu hút khách hàng
3. Nhấn mạnh các tính năng nổi bật từ tên sản phẩm
4. Không nói giá chi tiết kiểu '269.000 đồng' mà chỉ nói '269k' hoặc '429k'
5. Câu cuối cùng PHẢI là: 'Mọi người mua sản phẩm thì ấn vào link ở giỏ hàng nha.'
6. Viết bằng tiếng Việt tự nhiên, dễ nghe
7. Không dùng ký tự đặc biệt phức tạp
8. Đếm chính xác số ký tự để đảm bảo đúng ${TARGET_SCRIPT_LENGTH} ký tự

PHẦN 2 - TEXT OVERLAY:
Tạo một dòng text ngắn gọn, xúc tích (tối đa 70 ký tự) để hiển thị trên video, tóm tắt điểm nổi bật nhất của sản phẩm.
Ví dụ: 'Áo thun nam cao cấp - Giảm 37%' hoặc 'Giày sneaker đế êm - Chỉ 269k'

Hãy trả về kết quả dưới dạng JSON với format sau (KHÔNG thêm markdown code block):
{
  \"script\": \"<script voice-over với ${TARGET_SCRIPT_LENGTH} ký tự>\",
  \"overlay\": \"<text overlay ngắn gọn>\"
}"

# Call HuggingFace API
RESPONSE=$(curl -s --location "$HUGGINGFACE_ENDPOINT" \
  --header "Authorization: Bearer $HUGGINGFACE_API_KEY" \
  --header "Content-Type: application/json" \
  --data "{
    \"model\": \"$HUGGINGFACE_MODEL\",
    \"messages\": [
      {
        \"role\": \"user\",
        \"content\": $(echo "$PROMPT" | jq -Rs .)
      }
    ],
    \"max_tokens\": 1000,
    \"temperature\": 0.7
  }")

# Check if request was successful
if [ $? -ne 0 ]; then
    echo "Error calling HuggingFace API"
    exit 1
fi

# Extract the generated text (JSON response from AI)
GENERATED_TEXT=$(echo "$RESPONSE" | jq -r '.choices[0].message.content' 2>/dev/null)

if [ -z "$GENERATED_TEXT" ] || [ "$GENERATED_TEXT" = "null" ]; then
    echo "Error: No text generated from API"
    echo "Response: $RESPONSE"
    exit 1
fi

echo ""
echo "=== AI Response ==="
echo "$GENERATED_TEXT"
echo ""

# Parse JSON response to extract script and overlay
SCRIPT_TEXT=$(echo "$GENERATED_TEXT" | jq -r '.script' 2>/dev/null)
OVERLAY_TEXT=$(echo "$GENERATED_TEXT" | jq -r '.overlay' 2>/dev/null)

# Fallback: if JSON parsing fails, try to extract from markdown code block
if [ -z "$SCRIPT_TEXT" ] || [ "$SCRIPT_TEXT" = "null" ]; then
    # Try to extract JSON from markdown code block
    JSON_CONTENT=$(echo "$GENERATED_TEXT" | sed -n '/^```json/,/^```/p' | sed '1d;$d')
    if [ -n "$JSON_CONTENT" ]; then
        SCRIPT_TEXT=$(echo "$JSON_CONTENT" | jq -r '.script' 2>/dev/null)
        OVERLAY_TEXT=$(echo "$JSON_CONTENT" | jq -r '.overlay' 2>/dev/null)
    fi
fi

# Final validation
if [ -z "$SCRIPT_TEXT" ] || [ "$SCRIPT_TEXT" = "null" ]; then
    echo "Error: Could not parse script from AI response"
    exit 1
fi

if [ -z "$OVERLAY_TEXT" ] || [ "$OVERLAY_TEXT" = "null" ]; then
    echo "Warning: Could not parse overlay text, using product name as fallback"
    OVERLAY_TEXT="$PRODUCT_NAME"
fi

echo "=== Generated Script (${#SCRIPT_TEXT} characters) ==="
echo "$SCRIPT_TEXT"
echo ""
echo "=== Generated Overlay ==="
echo "$OVERLAY_TEXT"
echo ""

# Save to separate files
echo "$SCRIPT_TEXT" > scripts/generated_script.txt
echo "$OVERLAY_TEXT" > scripts/text_overlay.txt

echo "Script saved to scripts/generated_script.txt"
echo "Overlay saved to scripts/text_overlay.txt"
