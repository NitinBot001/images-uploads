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

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = '/tmp/images'  # Use /tmp for Render
app.config['ALLOWED_EXTENSIONS'] = {'png', 'jpg', 'jpeg', 'gif'}
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB limit for Render

# GitLab configuration (use environment variables for Render)
GITLAB_PAT = os.getenv('GITLAB_PAT', 'your-personal-access-token')  # Set in Render dashboard
GITLAB_PROJECT_ID = os.getenv('GITLAB_PROJECT_ID', '1212')  # e.g., '123456'
GITLAB_API_URL = f"https://gitlab.com/api/v4/projects/{GITLAB_PROJECT_ID}/repository"

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def allowed_file(filename):
    """Check if the file extension is allowed."""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

def generate_random_code():
    """Generate a 4-digit random code."""
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
        if file_size > 10 * 1024 * 1024:  # Warn if >10MB
            logger.warning(f"Image {new_filename} is {file_size / 1024 / 1024:.2f}MB")
        
        return new_filename
    except Exception as e:
        logger.error(f"Error processing image {file.filename}: {str(e)}")
        return None

def push_images_to_gitlab():
    """Push images to GitLab using REST API and clear the directory."""
    try:
        if not os.path.exists(app.config['UPLOAD_FOLDER']):
            logger.info("No images directory found.")
            return False, "No images directory found."
        
        image_files = [f for f in os.listdir(app.config['UPLOAD_FOLDER']) if os.path.isfile(os.path.join(app.config['UPLOAD_FOLDER'], f))]
        if not image_files:
            logger.info("No images to push.")
            return False, "No images to push."
        
        logger.info(f"Found {len(image_files)} images to push.")

        headers = {
            "Private-Token": GITLAB_PAT,
            "Content-Type": "application/json"
        }

        for image_file in image_files:
            image_path = os.path.join(app.config['UPLOAD_FOLDER'], image_file)
            gitlab_path = f"images/{image_file}"
            
            with open(image_path, 'rb') as f:
                content = base64.b64encode(f.read()).decode('utf-8')
            
            # Check if file exists by listing commits
            response = requests.get(
                f"{GITLAB_API_URL}/files/{requests.utils.quote(gitlab_path, safe='')}",
                headers=headers
            )
            
            payload = {
                "branch": "main",
                "content": content,
                "commit_message": f"Add or update image {image_file}",
                "encoding": "base64"
            }
            
            if response.status_code == 200:
                # File exists, update it
                put_response = requests.put(
                    f"{GITLAB_API_URL}/files/{requests.utils.quote(gitlab_path, safe='')}",
                    headers=headers,
                    data=json.dumps(payload)
                )
                if put_response.status_code not in (200, 201):
                    logger.error(f"Failed to update {image_file}: {put_response.text}")
                    continue
                logger.info(f"Updated {image_file} in GitLab.")
            else:
                # File does not exist, create it
                post_response = requests.post(
                    f"{GITLAB_API_URL}/files/{requests.utils.quote(gitlab_path, safe='')}",
                    headers=headers,
                    data=json.dumps(payload)
                )
                if post_response.status_code not in (200, 201):
                    logger.error(f"Failed to create {image_file}: {post_response.text}")
                    continue
                logger.info(f"Created {image_file} in GitLab.")
        
        if image_files:
            shutil.rmtree(app.config['UPLOAD_FOLDER'])
            logger.info("Cleared images directory after push.")
            return True, "Successfully pushed images to GitLab."
        else:
            return False, "No images were successfully pushed."
            
    except Exception as e:
        logger.error(f"Error pushing to GitLab: {str(e)}")
        return False, f"Error pushing to GitLab: {str(e)}"

def gitlab_push_worker():
    """Worker to push images to GitLab every 10 minutes."""
    while True:
        logger.info("Worker checking for images to push...")
        push_images_to_gitlab()
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
                'filename': new_filename
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
                        'message': f'Image saved as: {new_filename}'
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
            'message': 'Request too large or server error. Try uploading images individually via /api/upload.'
        }), 413

@app.route('/api/trigger-push', methods=['POST'])
def trigger_push():
    """Endpoint to manually trigger GitHub push."""
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
                            'filename': new_filename
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
                                'status': 'success'
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
