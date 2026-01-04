import os
import tempfile
from flask import Flask, render_template, request, send_file, flash, redirect, url_for
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = 'simple-key-for-railway'
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB

# Import AFTER Flask app is created to avoid circular imports
try:
    from dd1750_core import generate_dd1750_from_pdf
    HAS_DD1750 = True
except ImportError as e:
    print(f"WARNING: Could not import dd1750_core: {e}")
    HAS_DD1750 = False

def allowed_file(filename):
    return '.' in filename and filename.lower().endswith('.pdf')

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/generate', methods=['POST'])
def generate():
    if not HAS_DD1750:
        flash('Server configuration error. Please contact administrator.', 'error')
        return redirect(url_for('home'))
    
    if 'bom_file' not in request.files or 'template_file' not in request.files:
        flash('Please select both files', 'error')
        return redirect(url_for('home'))
    
    bom = request.files['bom_file']
    template = request.files['template_file']
    
    if bom.filename == '' or template.filename == '':
        flash('Please select both files', 'error')
        return redirect(url_for('home'))
    
    if not (allowed_file(bom.filename) and allowed_file(template.filename)):
        flash('Both files must be PDFs', 'error')
        return redirect(url_for('home'))
    
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            bom_path = os.path.join(tmpdir, 'bom.pdf')
            template_path = os.path.join(tmpdir, 'template.pdf')
            output_path = os.path.join(tmpdir, 'output.pdf')
            
            bom.save(bom_path)
            template.save(template_path)
            
            # Get start page
            try:
                start_page = int(request.form.get('start_page', 0))
            except:
                start_page = 0
            
            # Generate
            output_path, item_count = generate_dd1750_from_pdf(
                bom_path, template_path, output_path, start_page
            )
            
            if item_count == 0:
                flash('No items found. Try a different start page.', 'warning')
                return redirect(url_for('home'))
            
            return send_file(output_path, as_attachment=True, download_name='DD1750.pdf')
    
    except Exception as e:
        flash(f'Error: {str(e)}', 'error')
        return redirect(url_for('home'))

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=port)
