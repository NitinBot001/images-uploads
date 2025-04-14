from flask import Flask, request, jsonify, render_template
import os
import datetime
import random
import string
from PIL import Image
from werkzeug.utils import secure_filename
import logging
import requests
import base64
import json
import threading
import time
import shutil
import io
import urllib.parse

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = '/tmp/images'
app.config['ALLOWED_EXTENSIONS'] = {'png', 'jpg', 'jpeg', 'gif'}
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB for Render

# GitLab configuration
GITLAB_PAT = os.getenv('GITLAB_PAT', 'your-personal-access-token')
GITLAB_PROJECT_ID = os.getenv('GITLAB_PROJECT_ID', 'your-project-id')
GITLAB_API_URL = f"https://gitlab.com/api/v4/projects/{GITLAB_PROJECT_ID}/repository"
GITLAB_PROJECT_URL = f"https://gitlab.com/api/v4/projects/{GITLAB_PROJECT_ID}"

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

def generate_random_code():
    return ''.join(random.choices(string.digits, k=4))

def resize_and_save_image(file):
    """Resize and save the image with the specified format."""
    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        os.makedirs(app.config['UPLOAD_FOLDER'])
    
    now = datetime.datetime.now()
    timestamp = now.strftime("%d_%m_%y_%H_%M_%S")
    random_code = generate_random_code()
    file_extension = os.path.splitext(file.filename)[1].lower()
    new_filename = f"{timestamp}_{random_code}{file_extension}"
    destination_path = os.path.join(app.config['UPLOAD_FOLDER'], new_filename)
    
    try:
        img = Image.open(file)
        img.verify()
        file.seek(0)
        img = Image.open(file)
        if img.mode != 'RGB':
            img = img.convert('RGB')
        
        max_size = (1024, 1024)
        img.thumbnail(max_size, Image.Resampling.LANCZOS)
        
        if file_extension in ('.jpg', '.jpeg'):
            img.save(destination_path, 'JPEG', quality=85, optimize=True)
        else:
            img.save(destination_path)
        
        file_size = os.path.getsize(destination_path)
        logger.info(f"Processed {file.filename} as {new_filename}, size: {file_size / 1024 / 1024:.2f}MB")
        if file_size > 10 * 1024 * 1024:
            logger.warning(f"Image {new_filename} is large: {file_size / 1024 / 1024:.2f}MB")
        
        # Verify saved file
        with Image.open(destination_path) as img_verify:
            img_verify.verify()
        
        return new_filename
    except Exception as e:
        logger.error(f"Error processing image {file.filename}: {str(e)}")
        return None

def get_default_branch():
    """Get the default branch of the GitLab project."""
    try:
        headers = {"Private-Token": GITLAB_PAT}
        response = requests.get(GITLAB_PROJECT_URL, headers=headers)
        if response.status_code == 200:
            project_data = response.json()
            default_branch = project_data.get('default_branch', 'main')
            logger.info(f"Detected default branch: {default_branch}")
            return default_branch
        else:
            logger.error(f"Failed to get default branch: {response.status_code} - {response.text}")
            return 'main'
    except Exception as e:
        logger.error(f"Error getting default branch: {str(e)}")
        return 'main'

def ensure_images_directory(branch):
    """Ensure the images/ directory exists in GitLab."""
    try:
        headers = {"Private-Token": GITLAB_PAT, "Content-Type": "application/json"}
        # Check if images/ exists
        response = requests.get(
            f"{GITLAB_API_URL}/tree?path=images&ref={branch}",
            headers=headers
        )
        if response.status_code == 200:
            logger.info("images/ directory already exists.")
            return True
        
        # Create a dummy file to initialize the directory
        dummy_file = "images/.gitkeep"
        encoded_path = urllib.parse.quote(dummy_file, safe='')
        payload = {
            "branch": branch,
            "content": "",
            "commit_message": "Initialize images/ directory",
            "encoding": "text"
        }
        post_response = requests.post(
            f"{GITLAB_API_URL}/files/{encoded_path}",
            headers=headers,
            data=json.dumps(payload)
        )
        if post_response.status_code in (200, 201):
            logger.info("Created images/ directory with .gitkeep.")
            return True
        else:
            logger.error(f"Failed to create images/ directory: {post_response.status_code} - {post_response.text}")
            return False
    except Exception as e:
        logger.error(f"Error ensuring images/ directory: {str(e)}")
        return False

def push_images_to_gitlab():
    """Push images to GitLab and clear the directory."""
    try:
        if not os.path.exists(app.config['UPLOAD_FOLDER']):
            logger.info("No images directory found.")
            return False, "No images directory found."
        
        image_files = [f for f in os.listdir(app.config['UPLOAD_FOLDER']) if os.path.isfile(os.path.join(app.config['UPLOAD_FOLDER'], f))]
        if not image_files:
            logger.info("No images to push.")
            return False, "No images to push."
        
        logger.info(f"Found {len(image_files)} images to push: {image_files}")

        headers = {
            "Private-Token": GITLAB_PAT,
            "Content-Type": "application/json"
        }

        # Get default branch
        branch = get_default_branch()
        
        # Ensure images/ directory exists
        if not ensure_images_directory(branch):
            return False, "Failed to ensure images/ directory exists."

        success_count = 0
        failed_files = []
        for image_file in image_files:
            image_path = os.path.join(app.config['UPLOAD_FOLDER'], image_file)
            gitlab_path = f"images/{image_file}"
            encoded_path = urllib.parse.quote(gitlab_path, safe='')
            
            # Verify image before pushing
            try:
                with Image.open(image_path) as img_verify:
                    img_verify.verify()
            except Exception as e:
                logger.error(f"Image {image_file} corrupted before push: {str(e)}")
                failed_files.append(image_file)
                continue
            
            # Read and encode image
            with open(image_path, 'rb') as f:
                content = base64.b64encode(f.read()).decode('utf-8')
            
            # Check if file exists
            response = requests.get(
                f"{GITLAB_API_URL}/files/{encoded_path}",
                headers=headers,
                params={"ref": branch}
            )
            
            payload = {
                "branch": branch,
                "content": content,
                "commit_message": f"Add or update image {image_file}",
                "encoding": "base64"
            }
            
            api_success = False
            for attempt in range(3):  # Retry up to 3 times
                if response.status_code == 200:
                    put_response = requests.put(
                        f"{GITLAB_API_URL}/files/{encoded_path}",
                        headers=headers,
                        data=json.dumps(payload)
                    )
                    if put_response.status_code in (200, 201):
                        logger.info(f"Updated {image_file} in GitLab (attempt {attempt + 1}).")
                        api_success = True
                        break
                    else:
                        logger.error(f"Failed to update {image_file} (attempt {attempt + 1}): {put_response.status_code} - {put_response.text}")
                else:
                    post_response = requests.post(
                        f"{GITLAB_API_URL}/files/{encoded_path}",
                        headers=headers,
                        data=json.dumps(payload)
                    )
                    if post_response.status_code in (200, 201):
                        logger.info(f"Created {image_file} in GitLab (attempt {attempt + 1}).")
                        api_success = True
                        break
                    else:
                        logger.error(f"Failed to create {image_file} (attempt {attempt + 1}): {post_response.status_code} - {post_response.text}")
                time.sleep(1)  # Wait before retry
            
            if api_success:
                # Verify file exists
                verify_response = requests.get(
                    f"{GITLAB_API_URL}/files/{encoded_path}",
                    headers=headers,
                    params={"ref": branch}
                )
                if verify_response.status_code == 200:
                    logger.info(f"Verified {image_file} in GitLab repository.")
                    success_count += 1
                else:
                    logger.error(f"Verification failed for {image_file}: {verify_response.status_code} - {verify_response.text}")
                    failed_files.append(image_file)
            else:
                failed_files.append(image_file)
        
        if success_count == len(image_files):
            shutil.rmtree(app.config['UPLOAD_FOLDER'])
            logger.info("Cleared images directory after push.")
            return True, f"Successfully pushed {success_count} images to GitLab."
        else:
            message = f"Pushed {success_count} images, failed {len(failed_files)}: {failed_files}"
            logger.error(message)
            return False, message
            
    except Exception as e:
        logger.error(f"Error pushing to GitLab: {str(e)}")
        return False, f"Error pushing to GitLab: {str(e)}"

def gitlab_push_worker():
    """Worker to push images to GitLab every 10 minutes."""
    while True:
        logger.info("Worker checking for images to push...")
        success, message = push_images_to_gitlab()
        logger.info(message)
        time.sleep(600)

@app.route('/api/upload', methods=['POST'])
def upload_image():
    """Endpoint to upload a single image."""
    if 'file' not in request.files:
        return jsonify({
            'status': 'error',
            'message': 'No file part in the request'
        }), 400
    
    file = request.files['file']
    
    if file.filename == '':
        return jsonify({
            'status': 'error',
            'message': 'No file selected'
        }), 400
    
    if file and allowed_file(file.filename):
        new_filename = resize_and_save_image(file)
        if new_filename:
            return jsonify({
                'status': 'success',
                'message': f'Image uploaded and saved as: {new_filename}',
                'filename': new_filename,
                'url': f'https://easyfarms-assets.pages.dev/images/{new_filename}'
            }), 200
        else:
            return jsonify({
                'status': 'error',
                'message': 'Error processing image. Invalid or corrupted file.'
            }), 400
    else:
        return jsonify({
            'status': 'error',
            'message': 'Invalid file format. Allowed formats: png, jpg, jpeg, gif'
        }), 400

@app.route('/api/batch-upload', methods=['POST'])
def batch_upload_images():
    """Endpoint to upload multiple images in a batch."""
    try:
        if 'files' not in request.files:
            return jsonify({
                'status': 'error',
                'message': 'No files part in the request'
            }), 400
        
        files = request.files.getlist('files')
        
        if not files or all(file.filename == '' for file in files):
            return jsonify({
                'status': 'error',
                'message': 'No files selected'
            }), 400
        
        results = []
        for file in files:
            if file and allowed_file(file.filename):
                new_filename = resize_and_save_image(file)
                if new_filename:
                    results.append({
                        'filename': file.filename,
                        'new_filename': new_filename,
                        'status': 'success',
                        'message': f'Image saved as: {new_filename}',
                        'url': f'https://easyfarms-assets.pages.dev/images/{new_filename}'
                    })
                else:
                    results.append({
                        'filename': file.filename,
                        'status': 'error',
                        'message': 'Error processing image. Invalid or corrupted file.'
                    })
            else:
                results.append({
                    'filename': file.filename,
                    'status': 'error',
                    'message': 'Invalid file format. Allowed formats: png, jpg, jpeg, gif'
                })
        
        if all(result['status'] == 'error' for result in results):
            return jsonify({
                'status': 'error',
                'message': 'All uploads failed',
                'results': results
            }), 400
        
        status_code = 200 if any(result['status'] == 'success' for result in results) else 400
        return jsonify({
            'status': 'success' if status_code == 200 else 'partial_success',
            'message': 'Batch upload processed',
            'results': results
        }), status_code
    
    except Exception as e:
        logger.error(f"Batch upload error: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': 'Request too large or server error. Try uploading images individually.'
        }), 413

@app.route('/api/trigger-push', methods=['POST'])
def trigger_push():
    """Endpoint to manually trigger GitLab push."""
    success, message = push_images_to_gitlab()
    if success:
        return jsonify({
            'status': 'success',
            'message': message
        }), 200
    else:
        return jsonify({
            'status': 'error',
            'message': message
        }), 400

@app.route('/', methods=['GET', 'POST'])
def upload_page():
    """Render a simple upload page for testing."""
    if request.method == 'POST':
        try:
            if 'file' in request.files:
                file = request.files['file']
                if file and allowed_file(file.filename):
                    new_filename = resize_and_save_image(file)
                    if new_filename:
                        return jsonify({
                            'status': 'success',
                            'message': f'Image uploaded and saved as: {new_filename}',
                            'filename': new_filename,
                            'url': f'https://easyfarms-assets.pages.dev/images/{new_filename}'
                        }), 200
                    else:
                        return jsonify({
                            'status': 'error',
                            'message': 'Error processing image.'
                        }), 400
            elif 'files' in request.files:
                files = request.files.getlist('files')
                results = []
                for file in files:
                    if file and allowed_file(file.filename):
                        new_filename = resize_and_save_image(file)
                        if new_filename:
                            results.append({
                                'filename': file.filename,
                                'new_filename': new_filename,
                                'status': 'success',
                                'url': f'https://easyfarms-assets.pages.dev/images/{new_filename}'
                            })
                        else:
                            results.append({
                                'filename': file.filename,
                                'status': 'error'
                            })
                return jsonify({
                    'status': 'success',
                    'results': results
                }), 200
            return jsonify({
                'status': 'error',
                'message': 'No valid files provided'
            }), 400
        except Exception as e:
            logger.error(f"Web upload error: {str(e)}")
            return jsonify({
                'status': 'error',
                'message': 'Request too large or server error. Try uploading smaller images.'
            }), 413
    return render_template('upload.html')

# Start the worker thread
worker_thread = threading.Thread(target=gitlab_push_worker, daemon=True)
worker_thread.start()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
