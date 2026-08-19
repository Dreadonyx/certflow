import os
import pandas as pd
from PIL import Image, ImageDraw, ImageFont

def generate_certificates():
    # Configuration
    excel_file = "certificate (Responses).xlsx"
    template_img = "WhatsApp Image 2026-05-25 at 6.26.48 PM.jpeg"
    output_dir = "output_certs"
    
    # Text Placement Configuration
    # Adjusted based on screenshot: 
    # Y-coordinates moved to match the lines accurately
    Y_POS_NAME = 370
    Y_POS_COLLEGE = 430
    
    # X-coordinates for the center of the specific blank lines
    X_CENTER_NAME = 1000     # Center of the first line
    X_CENTER_COLLEGE = 650   # Center of the second line
    
    FONT_SIZE = 45
    TEXT_COLOR = (0, 0, 0) # Black text
    
    COLLEGE_NAME = "Velammal Engineering College"
    
    # Create output directory
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    # Load Excel data
    print("Loading data...")
    df = pd.read_excel(excel_file)
    
    # Load Roboto Font
    try:
        font = ImageFont.truetype("Roboto-Medium.ttf", FONT_SIZE)
    except IOError:
        print("Warning: Roboto font not found. Using default font.")
        font = ImageFont.load_default()

    print(f"Generating {len(df)} certificates...")
    
    for index, row in df.iterrows():
        name = str(row.get('Nam of the Participant (BLOCK LETTER)', '')).strip()
        initial = str(row.get('INITIAL', '')).strip()
        
        if pd.isna(name) or not name or name == 'nan':
            continue
            
        full_name = name
        if initial and initial.lower() != 'nan':
            full_name = f"{name} {initial}"
            
        img = Image.open(template_img).convert('RGB')
        draw = ImageDraw.Draw(img)
        
        # --- Draw Name ---
        if hasattr(draw, 'textbbox'):
            bbox_name = draw.textbbox((0, 0), full_name, font=font)
            text_width_name = bbox_name[2] - bbox_name[0]
        else:
            text_width_name, _ = draw.textsize(full_name, font=font)
            
        # Center around X_CENTER_NAME
        x_pos_name = X_CENTER_NAME - (text_width_name / 2)
        draw.text((x_pos_name, Y_POS_NAME), full_name, fill=TEXT_COLOR, font=font)
        
        # --- Draw College ---
        if hasattr(draw, 'textbbox'):
            bbox_college = draw.textbbox((0, 0), COLLEGE_NAME, font=font)
            text_width_college = bbox_college[2] - bbox_college[0]
        else:
            text_width_college, _ = draw.textsize(COLLEGE_NAME, font=font)
            
        # Center around X_CENTER_COLLEGE
        x_pos_college = X_CENTER_COLLEGE - (text_width_college / 2)
        draw.text((x_pos_college, Y_POS_COLLEGE), COLLEGE_NAME, fill=TEXT_COLOR, font=font)
        
        safe_name = "".join([c for c in full_name if c.isalpha() or c.isspace()]).rstrip()
        output_path = os.path.join(output_dir, f"{safe_name}.pdf")
        
        img.save(output_path, "PDF", resolution=100.0)
        print(f"Saved: {output_path}")

    print("All certificates generated successfully!")

if __name__ == "__main__":
    generate_certificates()
