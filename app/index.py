from flask import Flask, request, jsonify, render_template
import os
import datetime
import random
import string
from PIL import Image
from werkzeug.utils import secure_filename
import git
import shutil
import logging
from pathlib import Path

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = '/tmp/images'  # Use /tmp for Vercel
app.config['ALLOWED_EXTENSIONS'] = {'png', 'jpg', 'jpeg', 'gif'}

# GitHub configuration (use environment variables for Vercel)
GITHUB_REPO_URL = os.getenv('GITHUB_REPO_URL', 'https://github.com/NitinBot001/EasyFarms_assets.git')
GITHUB_PAT = os.getenv('GITHUB_PAT', 'your-personal-access-token')  # Set in Vercel dashboard
REPO_DIR = '/tmp/repo'  # Use /tmp for Vercel

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

def rename_and_save_image(file):
    """Rename and save the image with the specified format."""
    # Create images directory if it doesn't exist
    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        os.makedirs(app.config['UPLOAD_FOLDER'])
    
    # Get current timestamp
    now = datetime.datetime.now()
    timestamp = now.strftime("%d_%m_%y_%H_%M_%S")
    
    # Generate random 4-digit code
    random_code = generate_random_code()
    
    # Get file extension
    file_extension = os.path.splitext(file.filename)[1].lower()
    
    # Create new filename
    new_filename = f"{timestamp}_{random_code}{file_extension}"
    destination_path = os.path.join(app.config['UPLOAD_FOLDER'], new_filename)
    
    try:
        # Verify if it's a valid image
        img = Image.open(file)
        img.verify()  # Verify it's an image
        file.seek(0)  # Reset file pointer after verification
        
        # Save the image
        file.save(destination_path)
        return new_filename
    except Exception as e:
        logger.error(f"Error processing image {file.filename}: {str(e)}")
        return None

def push_images_to_github():
    """Check images directory and push to GitHub if images exist, then clear the directory."""
    try:
        # Check if images directory exists and has files
        if not os.path.exists(app.config['UPLOAD_FOLDER']):
            logger.info("No images directory found.")
            return False, "No images directory found."
        
        image_files = [f for f in os.listdir(app.config['UPLOAD_FOLDER']) if os.path.isfile(os.path.join(app.config['UPLOAD_FOLDER'], f))]
        if not image_files:
            logger.info("No images to push.")
            return False, "No images to push."
        
        logger.info(f"Found {len(image_files)} images to push.")

        # Initialize or clone the repository
        if not os.path.exists(REPO_DIR):
            # Clone the repository
            repo_url_with_pat = GITHUB_REPO_URL.replace('https://', f'https://{GITHUB_PAT}@')
            git.Repo.clone_from(repo_url_with_pat, REPO_DIR)
        repo = git.Repo(REPO_DIR)
        
        # Create images directory in repo if it doesn't exist
        repo_images_dir = os.path.join(REPO_DIR, 'images')
        if not os.path.exists(repo_images_dir):
            os.makedirs(repo_images_dir)
        
        # Copy images from app's images directory to repo's images directory
        for image_file in image_files:
            src_path = os.path.join(app.config['UPLOAD_FOLDER'], image_file)
            dst_path = os.path.join(repo_images_dir, image_file)
            shutil.copy2(src_path, dst_path)
        
        # Git operations
        repo.git.add('images/')
        if repo.is_dirty():
            commit_message = f"Add images {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            repo.index.commit(commit_message)
            
            # Push to remote
            origin = repo.remote(name='origin')
            origin.push()
            logger.info("Successfully pushed images to GitHub.")
            
            # Clear the images directory
            shutil.rmtree(app.config['UPLOAD_FOLDER'])
            logger.info("Cleared images directory after push.")
            return True, "Successfully pushed images to GitHub."
        else:
            logger.info("No changes to commit.")
            return False, "No changes to commit."
            
    except Exception as e:
        logger.error(f"Error pushing to GitHub: {str(e)}")
        return False, f"Error pushing to GitHub: {str(e)}"

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
        new_filename = rename_and_save_image(file)
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
            new_filename = rename_and_save_image(file)
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

@app.route('/api/trigger-push', methods=['POST'])
def trigger_push():
    """Endpoint to manually trigger GitHub push."""
    success, message = push_images_to_github()
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

# Optional: Web interface for testing
@app.route('/', methods=['GET', 'POST'])
def upload_page():
    """Render a simple upload page for testing."""
    if request.method == 'POST':
        if 'file' in request.files:
            file = request.files['file']
            if file and allowed_file(file.filename):
                new_filename = rename_and_save_image(file)
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
                    new_filename = rename_and_save_image(file)
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
    return render_template('upload.html')

# For Vercel compatibility (optional handler, not strictly needed)
def handler(event, context):
    return app

if __name__ == '__main__':
    # Run locally for development
    app.run(debug=True, host='0.0.0.0', port=5000)
