import pandas as pd
import re

def title_case(s):
    # Proper title case handling small words
    words = s.split()
    small_words = {'and', 'of', 'in', 'the', 'for', 'on', 'at', 'to'}
    title_words = []
    for i, word in enumerate(words):
        if i > 0 and word.lower() in small_words:
            title_words.append(word.lower())
        else:
            title_words.append(word.capitalize())
    return ' '.join(title_words)

def format_name(name, initial):
    name = str(name).strip()
    initial = str(initial).strip()
    
    # Remove "Dr." or "Dr" completely since it's hardcoded on the certificate
    name = re.sub(r'^(?i:dr\.?)\s*', '', name)
    
    full_name = name
    if initial:
        full_name += " " + initial
    return full_name

def format_designation(desig):
    d = str(desig).strip()
    d_lower = d.lower()
    
    # Neglect student/BE variations
    if d_lower in ['student', 's', 'be', 'b.e', 'b.e.', 'b.tech', '']:
        return ''
    
    # Basic Title Case
    d = title_case(d)
    
    # Fix Ph.D, Dr., and SG
    d = re.sub(r'(?i)\bphd\b', 'Ph.D', d)
    d = re.sub(r'(?i)\bph\.d\b', 'Ph.D', d)
    d = re.sub(r'(?i)\(sg\)', '(SG)', d)
    d = re.sub(r'(?i)senior grade', 'Senior Grade', d)
    
    if d_lower == 'doctor':
        d = 'Dr.'
        
    return d

def format_department(dept):
    d = str(dept).strip()
    if not d:
        return ''
        
    d_lower = d.lower()
    
    # Consolidate Similar Departments
    if 'cyber' in d_lower:
        dept = 'CSE (Cyber Security)'
    elif 'data science' in d_lower and 'artificial' in d_lower:
        dept = 'AI&DS'
    elif 'ai&ds' in d_lower or 'aids' in d_lower:
        dept = 'AI&DS'
    elif 'data science' in d_lower and 'computer' in d_lower:
        dept = 'CSE (Data Science)'
    elif d_lower in ['computer science and engineering', 'computer science', 'computer engineering', 'cse', 'cse(cs)']:
        dept = 'CSE'
    elif d_lower in ['electronics and communication engineering', 'electronic and communication engineering', 'ece']:
        dept = 'ECE'
    elif d_lower in ['information technology', 'it']:
        dept = 'Information Technology'
    else:
        # Title Case for any other unhandled departments
        dept = title_case(d)
        
    # Prepend "Department of "
    if not re.match(r'(?i)^department of', dept):
        dept = "Department of " + dept
        
    return dept

def format_college(college):
    c = str(college).strip()
    
    # Basic Title Case
    c = title_case(c)
    
    # Fix specific acronyms
    c = re.sub(r'(?i)\bsrmist\b', 'SRMIST', c)
    c = re.sub(r'(?i)\br&d\b', 'R&D', c)
    c = re.sub(r'(?i)\bm\.g\.r\.', 'M.G.R.', c)
    c = re.sub(r'(?i)mgr', 'MGR', c)
    c = re.sub(r'(?i)r\.m\.k\.', 'R.M.K.', c)
    c = re.sub(r'(?i)dr\.', 'Dr.', c)
    
    return c

def process_data():
    df = pd.read_excel('certificate (Responses).xlsx')
    df.fillna('', inplace=True)

    new_rows = []
    for index, row in df.iterrows():
        raw_name = str(row.get('Nam of the Participant (BLOCK LETTER)', ''))
        raw_initial = str(row.get('INITIAL', ''))
        raw_desig = str(row.get('Designation', ''))
        raw_dept = str(row.get('Department', ''))
        raw_college = str(row.get('Institution Name', ''))
        
        name_str = format_name(raw_name, raw_initial)
        desig_str = format_designation(raw_desig)
        dept_str = format_department(raw_dept)
        college_str = format_college(raw_college)
        
        # Column 1: Name + Initial, Designation, Department
        # Separating with ",  " (comma and two spaces)
        col1_parts = [name_str]
        if desig_str:
            col1_parts.append(desig_str)
        if dept_str:
            col1_parts.append(dept_str)
            
        col1 = ",  ".join(col1_parts)
        col2 = college_str
        
        # Clean potential multiple spaces inside parts, but keep our separator
        # Actually let's just use replace to ensure exactly 2 spaces after comma
        col1 = re.sub(r'\s*,\s*', ',  ', col1)
        
        new_rows.append({'Name_Designation_Department': col1, 'College': col2})

    out_df = pd.DataFrame(new_rows)
    out_df.to_csv('processed_certificates.csv', index=False)
    print("CSV file created successfully as 'processed_certificates.csv'")

if __name__ == '__main__':
    process_data()
