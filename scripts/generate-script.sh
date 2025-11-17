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
PROMPT="Hãy viết một đoạn giới thiệu sản phẩm cho video TikTok/Reels với các thông tin sau:

Tên sản phẩm: $PRODUCT_NAME
Giá hiện tại: $PRICE_FORMATTED
Giá gốc: $ORIGINAL_PRICE_FORMATTED
Giảm giá: $DISCOUNT

Yêu cầu:
1. QUAN TRỌNG: Script phải có CHÍNH XÁC ${TARGET_SCRIPT_LENGTH} ký tự (bao gồm dấu câu và khoảng trắng) để khớp với video dài ${VIDEO_DURATION} giây. Đây là yêu cầu BẮT BUỘC để script nói liền mạch từ đầu đến cuối video.
2. Giọng điệu hấp dẫn, thu hút khách hàng
3. Nhấn mạnh các tính năng nổi bật từ tên sản phẩm
4. Không nói giá chi tiết kiểu '269.000 đồng' mà chỉ nói '269k' hoặc '429k'
5. Câu cuối cùng PHẢI là: 'Mọi người mua sản phẩm thì ấn vào link ở bình luận nha.'
6. Viết bằng tiếng Việt tự nhiên, dễ nghe
7. Không dùng ký tự đặc biệt phức tạp
8. Đếm chính xác số ký tự để đảm bảo đúng ${TARGET_SCRIPT_LENGTH} ký tự

Hãy viết đoạn giới thiệu với CHÍNH XÁC ${TARGET_SCRIPT_LENGTH} ký tự:"

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

# Extract the generated text
GENERATED_TEXT=$(echo "$RESPONSE" | jq -r '.choices[0].message.content' 2>/dev/null)

if [ -z "$GENERATED_TEXT" ] || [ "$GENERATED_TEXT" = "null" ]; then
    echo "Error: No text generated from API"
    echo "Response: $RESPONSE"
    exit 1
fi

echo ""
echo "=== Generated Script ==="
echo "$GENERATED_TEXT"
echo ""

# Save to file
echo "$GENERATED_TEXT" > scripts/generated_script.txt

echo "Script saved to scripts/generated_script.txt"
