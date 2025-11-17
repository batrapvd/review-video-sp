#!/usr/bin/env python3
"""
Video Processing Script
Database-driven video processing with cloud storage integration
"""

import os
import sys
import json
import subprocess
import psycopg2
from psycopg2.extras import RealDictCursor
import boto3
from botocore.client import Config
from datetime import datetime
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import tempfile
import shutil

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class VideoProcessor:
    """Main video processing class"""

    def __init__(self):
        """Initialize with environment variables"""
        # Database config
        self.db_url = os.getenv('DATABASE_URL')
        if not self.db_url:
            raise ValueError("DATABASE_URL environment variable is required")

        # R2 config
        self.r2_access_key = os.getenv('R2_ACCESS_KEY_ID')
        self.r2_secret_key = os.getenv('R2_SECRET_ACCESS_KEY')
        self.r2_endpoint = os.getenv('R2_ENDPOINT')
        self.r2_bucket = os.getenv('R2_BUCKET_NAME', 'yt-2-tiktok')

        if not all([self.r2_access_key, self.r2_secret_key, self.r2_endpoint]):
            raise ValueError("R2 credentials (R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_ENDPOINT) are required")

        # AI/TTS config
        self.huggingface_endpoint = os.getenv('HUGGINGFACE_ENDPOINT', 'https://router.huggingface.co/v1/chat/completions')
        self.huggingface_model = os.getenv('HUGGINGFACE_MODEL', 'deepseek-ai/DeepSeek-V3.2-Exp')
        self.huggingface_api_key = os.getenv('HUGGINGFACE_API_KEY')
        self.zalo_api_key = os.getenv('ZALO_API_KEY')

        # Working directories
        self.base_dir = Path(__file__).parent
        self.videos_dir = self.base_dir / 'videos'
        self.output_dir = self.base_dir / 'output'
        self.scripts_dir = self.base_dir / 'scripts'

        # Initialize R2 client
        self.r2_client = boto3.client(
            's3',
            endpoint_url=self.r2_endpoint,
            aws_access_key_id=self.r2_access_key,
            aws_secret_access_key=self.r2_secret_key,
            config=Config(signature_version='s3v4')
        )

    def setup_directories(self):
        """Create necessary working directories"""
        for directory in [self.videos_dir, self.output_dir, self.scripts_dir]:
            directory.mkdir(exist_ok=True)
            logger.info(f"Directory ready: {directory}")

    def cleanup_directories(self):
        """Clean up working directories"""
        for directory in [self.videos_dir, self.output_dir]:
            if directory.exists():
                shutil.rmtree(directory)
                directory.mkdir(exist_ok=True)
        logger.info("Cleaned up working directories")

    def get_pending_products(self) -> List[Dict]:
        """Fetch products from database where merge_status=FALSE and crawl_status=TRUE"""
        try:
            conn = psycopg2.connect(self.db_url)
            cursor = conn.cursor(cursor_factory=RealDictCursor)

            query = """
                SELECT id, video_data
                FROM public.products
                WHERE merge_status = FALSE AND crawl_status = TRUE
                ORDER BY id
            """

            cursor.execute(query)
            products = cursor.fetchall()

            cursor.close()
            conn.close()

            logger.info(f"Found {len(products)} pending products")
            return products

        except Exception as e:
            logger.error(f"Database error: {e}")
            raise

    def update_merge_status(self, product_id: int, r2_url: str):
        """Update merge_status to TRUE after successful processing"""
        try:
            conn = psycopg2.connect(self.db_url)
            cursor = conn.cursor()

            query = """
                UPDATE public.products
                SET merge_status = TRUE,
                    r2_video_url = %s,
                    processed_at = NOW()
                WHERE id = %s
            """

            cursor.execute(query, (r2_url, product_id))
            conn.commit()

            cursor.close()
            conn.close()

            logger.info(f"Updated product {product_id} merge_status to TRUE")

        except Exception as e:
            logger.error(f"Failed to update database: {e}")
            raise

    def update_crawl_status(self, product_id: int, status: bool = False):
        """Update crawl_status (set to FALSE when videos are invalid/expired)"""
        try:
            conn = psycopg2.connect(self.db_url)
            cursor = conn.cursor()

            query = """
                UPDATE public.products
                SET crawl_status = %s
                WHERE id = %s
            """

            cursor.execute(query, (status, product_id))
            conn.commit()

            cursor.close()
            conn.close()

            logger.info(f"Updated product {product_id} crawl_status to {status}")

        except Exception as e:
            logger.error(f"Failed to update crawl_status: {e}")
            raise

    def process_product(self, product_id: int, video_data: Dict) -> Optional[str]:
        """
        Process a single product: download videos, merge, add audio/text, upload to R2
        Returns R2 URL if successful, None otherwise
        """
        try:
            # Validate video_data is not None
            if video_data is None:
                logger.error(f"Product {product_id}: video_data is NULL - skipping")
                return None

            # Validate video_data has required structure
            if not isinstance(video_data, dict):
                logger.error(f"Product {product_id}: video_data is not a dict - skipping")
                return None

            # Validate has videos array
            videos = video_data.get('videos', [])
            if not videos or not isinstance(videos, list) or len(videos) == 0:
                logger.error(f"Product {product_id}: no videos found in video_data - skipping")
                return None

            product_name = video_data.get('productInfo', {}).get('name', 'Unknown')
            logger.info(f"Processing product {product_id}: {product_name}")

            # Save video data to JSON file for existing scripts to use
            video_data_file = self.base_dir / 'video-data.json'
            with open(video_data_file, 'w', encoding='utf-8') as f:
                json.dump(video_data, f, ensure_ascii=False, indent=2)

            # Download videos
            download_success, error_code = self.download_videos(video_data)
            if not download_success:
                # If videos are expired/not found (404), update crawl_status to FALSE
                if error_code == '404':
                    logger.warning(f"Product {product_id}: Videos not found (404) - updating crawl_status to FALSE for re-crawling")
                    try:
                        self.update_crawl_status(product_id, False)
                    except Exception as e:
                        logger.error(f"Failed to update crawl_status: {e}")
                return None

            # Process videos (trim)
            if not self.process_videos(video_data):
                return None

            # Merge videos
            if not self.merge_videos(video_data):
                return None

            # Generate AI script
            if not self.generate_script(video_data_file):
                return None

            # Generate audio
            if not self.generate_audio():
                return None

            # Add audio to video
            if not self.add_audio():
                return None

            # Add text overlay (using AI-generated overlay text)
            if not self.add_text_overlay(video_data):
                return None

            # Upload to R2
            final_video = self.output_dir / 'final_merged_video.mp4'
            r2_url = self.upload_to_r2(final_video, product_id, video_data)

            return r2_url

        except Exception as e:
            logger.error(f"Error processing product {product_id}: {e}")
            return None

    def download_videos(self, video_data: Dict) -> Tuple[bool, Optional[str]]:
        """
        Download all videos from URLs
        Returns: (success: bool, error_code: Optional[str])
        error_code can be '404', '403', 'invalid', etc.
        """
        try:
            videos = video_data.get('videos', [])
            logger.info(f"Downloading {len(videos)} videos...")

            for i, video in enumerate(videos):
                url = video.get('url')
                output_path = self.videos_dir / f'video_{i}.mp4'

                # Download with retry
                max_retries = 3
                last_http_code = None
                for retry in range(max_retries):
                    try:
                        # Use headers to bypass Shopee restrictions
                        result = subprocess.run([
                            'curl', '-L', '-o', str(output_path), url,
                            '--max-time', '300',
                            '--connect-timeout', '30',
                            '-H', 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                            '-H', 'Referer: https://shopee.vn/',
                            '-H', 'Accept: video/webm,video/ogg,video/*;q=0.9,application/ogg;q=0.7,audio/*;q=0.6,*/*;q=0.5',
                            '-H', 'Accept-Language: vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7',
                            '-w', '%{http_code}',
                            '--compressed'
                        ], capture_output=True, text=True)

                        http_code = result.stdout.strip() if result.stdout else '000'
                        last_http_code = http_code
                        logger.info(f"Video {i+1} download HTTP status: {http_code}")

                        # Check if file was created and has content
                        if not output_path.exists():
                            raise Exception(f"File was not created (HTTP {http_code})")

                        file_size = output_path.stat().st_size
                        if file_size == 0:
                            raise Exception(f"Downloaded file is empty (HTTP {http_code})")

                        if file_size < 1024:  # Less than 1KB is suspicious
                            logger.warning(f"Video {i+1} file size is very small: {file_size} bytes (HTTP {http_code})")
                            # Read first bytes to check if it's an error page
                            with open(output_path, 'rb') as f:
                                first_bytes = f.read(100)
                                logger.warning(f"First bytes: {first_bytes[:50]}")

                        # Validate with ffprobe
                        validate_result = subprocess.run([
                            'ffprobe', '-v', 'error',
                            '-show_entries', 'format=duration',
                            '-of', 'default=noprint_wrappers=1:nokey=1',
                            str(output_path)
                        ], capture_output=True, text=True)

                        if validate_result.returncode != 0:
                            logger.error(f"Video {i+1} validation failed: {validate_result.stderr}")
                            raise Exception(f"Invalid video file (HTTP {http_code}): {validate_result.stderr}")

                        logger.info(f"Downloaded video {i+1}/{len(videos)} ({file_size} bytes)")
                        break

                    except Exception as download_error:
                        if retry < max_retries - 1:
                            logger.warning(f"Download failed ({download_error}), retrying... ({retry+1}/{max_retries})")
                            # Clean up failed download
                            if output_path.exists():
                                output_path.unlink()
                            continue
                        else:
                            logger.error(f"Failed to download video {i+1} after {max_retries} attempts: {download_error}")
                            logger.error(f"URL: {url}")
                            # Return error code for proper handling
                            return (False, last_http_code)

            return (True, None)

        except Exception as e:
            logger.error(f"Error downloading videos: {e}")
            return (False, None)

    def process_videos(self, video_data: Dict) -> bool:
        """Trim 2 seconds from start and end of each video"""
        try:
            videos = video_data.get('videos', [])
            logger.info("Processing videos (trimming)...")

            for i in range(len(videos)):
                input_path = self.videos_dir / f'video_{i}.mp4'
                output_path = self.videos_dir / f'trimmed_{i}.mp4'

                # Check if input file exists
                if not input_path.exists():
                    logger.error(f"Video {i+1} file not found: {input_path}")
                    return False

                # Get duration
                try:
                    result = subprocess.run([
                        'ffprobe', '-v', 'error',
                        '-show_entries', 'format=duration',
                        '-of', 'default=noprint_wrappers=1:nokey=1',
                        str(input_path)
                    ], capture_output=True, text=True, check=True)

                    duration = float(result.stdout.strip())
                except subprocess.CalledProcessError as e:
                    logger.error(f"ffprobe failed for video {i+1}: {e.stderr}")
                    return False
                except ValueError as e:
                    logger.error(f"Invalid duration for video {i+1}: {result.stdout}")
                    return False

                new_duration = duration - 4

                if new_duration > 0:
                    # Trim video
                    try:
                        trim_result = subprocess.run([
                            'ffmpeg', '-i', str(input_path),
                            '-ss', '2', '-t', str(new_duration),
                            '-c:v', 'libx264', '-preset', 'medium', '-crf', '23',
                            '-c:a', 'aac', '-b:a', '128k', '-ar', '48000',
                            '-r', '30',
                            '-y', str(output_path)
                        ], capture_output=True, text=True, check=True)

                        logger.info(f"Trimmed video {i+1}: {duration:.2f}s -> {new_duration:.2f}s")
                    except subprocess.CalledProcessError as e:
                        logger.error(f"ffmpeg trim failed for video {i+1}: {e.stderr}")
                        return False
                else:
                    # Video too short, keep original
                    shutil.copy(input_path, output_path)
                    logger.warning(f"Video {i+1} too short ({duration:.2f}s), keeping original")

            return True

        except Exception as e:
            logger.error(f"Error processing videos: {e}")
            return False

    def merge_videos(self, video_data: Dict) -> bool:
        """Merge all trimmed videos into one"""
        try:
            logger.info("Merging videos...")

            # Create concat list
            concat_file = self.videos_dir / 'concat_list.txt'
            videos = video_data.get('videos', [])

            with open(concat_file, 'w') as f:
                for i in range(len(videos)):
                    f.write(f"file 'trimmed_{i}.mp4'\n")

            # Merge with ffmpeg
            output_path = self.output_dir / 'merged_temp.mp4'

            subprocess.run([
                'ffmpeg', '-f', 'concat', '-safe', '0',
                '-i', str(concat_file),
                '-c:v', 'libx264', '-preset', 'medium', '-crf', '23',
                '-c:a', 'aac', '-b:a', '128k', '-ar', '48000',
                '-r', '30',
                '-movflags', '+faststart',
                '-y', str(output_path)
            ], check=True, capture_output=True)

            logger.info("Videos merged successfully")
            return True

        except Exception as e:
            logger.error(f"Error merging videos: {e}")
            return False

    def get_video_duration(self, video_path: Path) -> float:
        """Get video duration in seconds"""
        try:
            result = subprocess.run([
                'ffprobe', '-v', 'error',
                '-show_entries', 'format=duration',
                '-of', 'default=noprint_wrappers=1:nokey=1',
                str(video_path)
            ], capture_output=True, text=True, check=True)

            duration = float(result.stdout.strip())
            return duration

        except Exception as e:
            logger.error(f"Error getting video duration: {e}")
            return 0.0

    def calculate_target_script_length(self, video_duration: float, chars_per_second: float = 15.0) -> int:
        """
        Calculate target script length based on video duration and speech speed

        Args:
            video_duration: Video duration in seconds
            chars_per_second: Average Vietnamese speech speed (default: 15 chars/sec)

        Returns:
            Target script length in characters
        """
        target_length = int(video_duration * chars_per_second)
        logger.info(f"Video duration: {video_duration:.2f}s, Target script length: {target_length} characters")
        return target_length

    def generate_script(self, video_data_file: Path) -> bool:
        """Generate AI script using existing bash script with calculated target length"""
        try:
            logger.info("Generating AI script...")

            # Get merged video duration
            merged_video = self.output_dir / 'merged_temp.mp4'
            video_duration = self.get_video_duration(merged_video)

            if video_duration == 0:
                logger.error("Failed to get video duration")
                return False

            # Calculate target script length
            target_length = self.calculate_target_script_length(video_duration)

            env = os.environ.copy()
            env['HUGGINGFACE_ENDPOINT'] = self.huggingface_endpoint
            env['HUGGINGFACE_MODEL'] = self.huggingface_model
            env['HUGGINGFACE_API_KEY'] = self.huggingface_api_key
            env['TARGET_SCRIPT_LENGTH'] = str(target_length)
            env['VIDEO_DURATION'] = str(video_duration)

            script_path = self.scripts_dir / 'generate-script.sh'

            subprocess.run([
                'bash', str(script_path), str(video_data_file)
            ], check=True, env=env, capture_output=True)

            logger.info("AI script generated successfully")
            return True

        except Exception as e:
            logger.error(f"Error generating script: {e}")
            return False

    def generate_audio(self) -> bool:
        """Generate audio using existing bash script"""
        try:
            logger.info("Generating audio...")

            env = os.environ.copy()
            if self.zalo_api_key:
                env['ZALO_API_KEY'] = self.zalo_api_key

            script_path = self.scripts_dir / 'generate-audio.sh'
            text_file = self.scripts_dir / 'generated_script.txt'

            subprocess.run([
                'bash', str(script_path), str(text_file)
            ], check=True, env=env)

            audio_file = self.output_dir / 'voiceover.wav'
            if not audio_file.exists():
                logger.error("Audio file not generated")
                return False

            logger.info("Audio generated successfully")
            return True

        except Exception as e:
            logger.error(f"Error generating audio: {e}")
            return False

    def add_audio(self) -> bool:
        """Add audio to merged video"""
        try:
            logger.info("Adding audio to video...")

            # Normalize audio
            subprocess.run([
                'ffmpeg', '-i', str(self.output_dir / 'voiceover.wav'),
                '-ar', '48000', '-ac', '2', '-c:a', 'aac', '-b:a', '192k',
                '-y', str(self.output_dir / 'voiceover_normalized.aac')
            ], check=True, capture_output=True)

            # Add audio to video
            subprocess.run([
                'ffmpeg',
                '-i', str(self.output_dir / 'merged_temp.mp4'),
                '-i', str(self.output_dir / 'voiceover_normalized.aac'),
                '-map', '0:v', '-map', '1:a',
                '-c:v', 'libx264', '-preset', 'medium', '-crf', '23',
                '-c:a', 'copy',
                '-shortest',
                '-y', str(self.output_dir / 'merged_with_audio.mp4')
            ], check=True, capture_output=True)

            logger.info("Audio added to video successfully")
            return True

        except Exception as e:
            logger.error(f"Error adding audio: {e}")
            return False

    def add_text_overlay(self, video_data: Dict) -> bool:
        """Add AI-generated text overlay to video"""
        try:
            logger.info("Adding text overlay...")

            # Read AI-generated overlay text from file
            overlay_file = self.scripts_dir / 'text_overlay.txt'

            if overlay_file.exists():
                with open(overlay_file, 'r', encoding='utf-8') as f:
                    overlay_text = f.read().strip()
                logger.info(f"Using AI-generated overlay: {overlay_text}")
            else:
                # Fallback to product name if overlay file doesn't exist
                overlay_text = video_data.get('productInfo', {}).get('name', 'Product')
                logger.warning(f"Overlay file not found, using product name: {overlay_text}")

            # Get video dimensions
            result = subprocess.run([
                'ffprobe', '-v', 'error',
                '-select_streams', 'v:0',
                '-show_entries', 'stream=width,height',
                '-of', 'csv=s=x:p=0',
                str(self.output_dir / 'merged_with_audio.mp4')
            ], capture_output=True, text=True, check=True)

            dimensions = result.stdout.strip()
            logger.info(f"Video dimensions: {dimensions}")

            # Determine font size based on text length
            text_length = len(overlay_text)

            if text_length > 70:
                fontsize = 28
            elif text_length > 50:
                fontsize = 32
            else:
                fontsize = 38

            # Escape text for ffmpeg
            escaped_text = overlay_text.replace("'", "'\\''").replace(":", "\\:")

            # Add text overlay
            subprocess.run([
                'ffmpeg',
                '-i', str(self.output_dir / 'merged_with_audio.mp4'),
                '-vf', f"drawtext=text='{escaped_text}':fontsize={fontsize}:fontcolor=white:x=(w-text_w)/2:y=60:box=1:boxcolor=black@0.85:boxborderw=25",
                '-c:a', 'copy',
                '-y', str(self.output_dir / 'final_merged_video.mp4')
            ], check=True, capture_output=True)

            logger.info("Text overlay added successfully")
            return True

        except Exception as e:
            logger.error(f"Error adding text overlay: {e}")
            return False

    def _wrap_text(self, text: str, lines: int) -> str:
        """Wrap text into multiple lines"""
        length = len(text)
        chunk_size = length // lines

        result_lines = []
        current_pos = 0

        for i in range(lines - 1):
            # Find best split point near chunk boundary
            target = current_pos + chunk_size
            best_split = target

            # Search for space/comma/dash near target
            for offset in range(20):
                for pos in [target + offset, target - offset]:
                    if pos < length and pos > current_pos:
                        if text[pos] in ' ,-':
                            best_split = pos
                            break
                if best_split != target:
                    break

            result_lines.append(text[current_pos:best_split].strip())
            current_pos = best_split

        # Add remaining text as last line
        result_lines.append(text[current_pos:].strip())

        return '\\n'.join(result_lines)

    def upload_to_r2(self, video_path: Path, product_id: int, video_data: Dict) -> Optional[str]:
        """Upload video to Cloudflare R2"""
        try:
            logger.info("Uploading to Cloudflare R2...")

            # Generate R2 key
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            product_name_slug = video_data.get('productInfo', {}).get('name', 'product')
            # Clean filename
            product_name_slug = ''.join(c if c.isalnum() or c in '-_' else '_' for c in product_name_slug)[:50]

            r2_key = f"merged_videos/{timestamp}_product_{product_id}_{product_name_slug}.mp4"

            # Upload to R2
            with open(video_path, 'rb') as f:
                self.r2_client.put_object(
                    Bucket=self.r2_bucket,
                    Key=r2_key,
                    Body=f,
                    ContentType='video/mp4',
                    Metadata={
                        'product_id': str(product_id),
                        'processed_at': datetime.now().isoformat()
                    }
                )

            # Generate public URL
            r2_public_url = f"https://pub-09ecd227972848afb3d86c1f7f2b57b1.r2.dev/{r2_key}"

            logger.info(f"Video uploaded to R2: {r2_public_url}")
            return r2_public_url

        except Exception as e:
            logger.error(f"Error uploading to R2: {e}")
            return None

    def run(self):
        """Main processing loop"""
        logger.info("Starting video processing...")

        # Setup directories
        self.setup_directories()

        # Get pending products
        products = self.get_pending_products()

        if not products:
            logger.info("No pending products to process")
            return

        # Process each product
        success_count = 0
        failed_count = 0
        skipped_count = 0

        for product in products:
            product_id = product['id']
            video_data = product['video_data']

            # Validate product data before processing
            if video_data is None:
                logger.warning(f"⚠️  Product {product_id}: video_data is NULL - skipping")
                skipped_count += 1
                continue

            if not isinstance(video_data, dict):
                logger.warning(f"⚠️  Product {product_id}: video_data is not valid JSON - skipping")
                skipped_count += 1
                continue

            videos = video_data.get('videos', [])
            if not videos or not isinstance(videos, list) or len(videos) == 0:
                logger.warning(f"⚠️  Product {product_id}: no videos in video_data - skipping")
                skipped_count += 1
                continue

            # Clean up before processing each product
            self.cleanup_directories()

            # Process product
            r2_url = self.process_product(product_id, video_data)

            if r2_url:
                # Update database
                try:
                    self.update_merge_status(product_id, r2_url)
                    success_count += 1
                    logger.info(f"✅ Product {product_id} processed successfully")
                except Exception as e:
                    logger.error(f"Failed to update database for product {product_id}: {e}")
                    failed_count += 1
            else:
                failed_count += 1
                logger.error(f"❌ Failed to process product {product_id}")

        # Summary
        logger.info("=" * 50)
        logger.info(f"Processing complete!")
        logger.info(f"Success: {success_count}")
        logger.info(f"Failed: {failed_count}")
        logger.info(f"Skipped (invalid data): {skipped_count}")
        logger.info(f"Total: {len(products)}")
        logger.info("=" * 50)


def main():
    """Entry point"""
    try:
        processor = VideoProcessor()
        processor.run()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
