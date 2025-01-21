from pydicom import dcmread
import os
from pathlib import Path
import re
import pandas as pd
import sqlite3
import json
import datetime
from tkinter import filedialog

today = datetime.date.today()


def select_folder():
    # 開啟選擇目錄對話框
    folder_selected = filedialog.askdirectory(
        title='請選擇要讀取的母資料夾', initialdir=Path('.')
    )
    
    if folder_selected:  # 確認使用者有選擇資料夾
        # 將選擇的路徑轉換成 Path 物件
        current_directory = Path(folder_selected)
    else:
        current_directory = 0
    return current_directory


def processing(metadata):
    print('讀取dicom資料.....')
    current_directory = Path(metadata['path'])
    
    if not current_directory.exists():
        print('該路徑不存在!請重新選取')
        current_directory = select_folder()
        if current_directory == 0:
            return 0
        
    
    subdir = [i for i in Path(current_directory).iterdir() if i.is_dir()== True]
    subdir_file = [list(s.glob('*.dcm')) for s in subdir]
    
    if len(subdir_file)==0 or len(subdir)==0:
        print('無子目錄或目錄下無dicom檔案')
        return 0
    
    #讀取相關檔案區    
    subdir_file = [item[0] for item in subdir_file]

    name_list =[]
    id_list = []
    site_list = []
    study_date_list = []
    study_id_list = []
    ctdivol_list = []
    dlp_number_list = []
    comment_list = []
    event_rec_list = []
    acq_type_list = []

    for f in subdir_file:
        obj = dcmread(f)
        pt_name = obj.PatientName
        pt_name = str(pt_name).split('^')[0]
        #print(pt_name)
        pt_id = obj.PatientID
        if obj.ImageType[-1] != 'SCREEN SAVE':
            print(f'Patient {pt_id} {pt_name} 影像非螢幕截圖')
            pass
        else:
            site = obj.StudyDescription
            study_date = obj.StudyDate
            study_date = study_date[0:4]+'-'+study_date[4:6]+'-'+study_date[6:8]
            study_id = obj.StudyID
            
            ctdivol: list=[]
            event_no: list =[]
            acq_type: list = []
            exposure_sq= obj.ExposureDoseSequence
            for event, sq in enumerate(exposure_sq):
                #print(sq)
                try:
                    ctdivol.append(sq[0x00189345].value)
                    event_no.append(event+1)
                    
                    if sq.AcquisitionType == 'STATIONARY':
                        acq_type.append('4DCT')
                    else:
                        acq_type.append(sq.AcquisitionType)
                    
                    
                except:
                    pass
            
            if len(ctdivol)>0:
                ctdivol_list.append(ctdivol[-1])
                event_rec_list.append(event_no[-1])
                acq_type_list.append(acq_type[-1])
            else:
                ctdivol_list.append(None)
                event_rec_list.append(None)
                acq_type_list.append(None)
                
            
            try:
                text = obj.CommentsOnRadiationDose
                # 使用正則表達式查找 ' DLP=' 後的數字 (注意DLP=前面要有空格，不然total DLP也會帶進去)
                dlp_values = re.findall(r" DLP=([\d.]+)", text)
                # 將提取的數字轉為浮點數，取最後一個item
                dlp_number = [float(value) for value in dlp_values][-1]
            except:
                text = None
                dlp_number = None
                print(f'Patient {pt_id} {pt_name}, 沒有DLP數值')
            
            study_id_list.append(int(study_id))
            study_date_list.append(study_date)
            id_list.append(pt_id)
            name_list.append(pt_name)
            
            dlp_number_list.append(dlp_number)
            site_list.append(site)
            comment_list.append(text)

    #組成dataframe檔案
    output = pd.DataFrame({'study_ID':study_id_list, 'study_date':study_date_list, 
                'sites':site_list,'ID':id_list, 'Name':name_list, 
                'acq_type':acq_type_list, 'CTDIVol':ctdivol_list, 
                'DLP':dlp_number_list,
                'Rec_event':event_rec_list, 'Comment':comment_list })
    return output


def backup_sql(output):
    db_path = 'CTdose_eval.db'
    print('備份到sqlite .....')
    def append_sql(output, connection):
        connection.execute("PRAGMA synchronous = OFF;")
        for i, item in output.iterrows():
            #study_ID為唯一值，若不小心有重複的則予以忽略
            connection.execute("""
                            INSERT OR IGNORE INTO CT_dose (study_ID,study_date,sites,ID,Name,acq_type,CTDIVol,DLP,Rec_event,Comment) 
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """, 
                                (item['study_ID'], item['study_date'], item['sites'],
                                item['ID'], item['Name'], item['acq_type'],
                                item['CTDIVol'], item['DLP'], item['Rec_event'], item['Comment']
                                ))                   
        return connection.commit()
    
    #若CTdose_eval.db存在
    if os.path.exists(db_path):
        connection = sqlite3.connect('CTdose_eval.db')
        append_sql(output, connection)
        
     
    #若不存在則建立新的   
    else:
        connection = sqlite3.connect('CTdose_eval.db')
        output.to_sql('CT_dose', connection, if_exists='fail', index=False)
        #設定study_ID為key_index
        connection.execute("CREATE UNIQUE INDEX idx_key_index ON CT_dose (study_ID)")
        
    ct_database = pd.read_sql_query('Select * FROM CT_dose', connection, parse_dates=['study_date'])
    connection.close()
    
    return ct_database


def export_csv(ct_database, metadata):
    print('輸出到csv .....')
    year_label = list(set(int(str(i)[0:4]) for i in ct_database['study_date'].unique()))
    #讀取忽略的指定年份
    omit_year = metadata['omit_year']
    omit_year = [int(i) for i in omit_year if i in year_label]
    if len(omit_year) !=0:
        print(f'\n本次忽略輸出的有效年份為:{omit_year}')
    else:
        print(f'\n本次無忽略的年份，將全數輸出')
    output_year = list(set(year_label)-set(omit_year))
     
    #讀取年份   
    for yr in output_year:
        
        sub_df = ct_database[ct_database['study_date'].dt.year==int(yr)]
        sub_df = sub_df.sort_values(by='study_ID')
        #讀取sites名稱
        sites_label = list(sub_df['sites'].unique())
        
        for site in sites_label:
            site_df = sub_df[sub_df['sites']==site]
            
            base_path = Path('.')
            dir_path = base_path / 'output'
            dir_path.mkdir(parents=True, exist_ok=True)
            filename = f'{yr}_{site}.csv'
            dir_path = dir_path / filename
            site_df.to_csv(dir_path, index=False)
    print('已輸出到output子目錄中')
    
    today = datetime.date.today()

    metadata: dict= {'說明1':'path為路徑名稱，有預設值，若為"\"符號請重複兩次',
                     '說明2':'last_reading_date為最後讀取日期，當執行時候必為當日，不用更改',
                     '說明3':'omit_year為忽略年分，請在方括號裡面填入不要輸出的年份並用,號分開',
                    'path':metadata['path'], 'last_reading_date':str(today), 'omit_year':omit_year}
    with open("metadata.json", "w", encoding="utf-8") as json_file:
        json.dump(metadata, json_file, ensure_ascii=False, indent=4)       
        
        
        
        



# Main Section
metadata:dict = {'path':'.'}

try:
    with open ('metadata.json', 'r', encoding='utf-8') as json_file:
        metadata=json.load(json_file)
    print(f'讀取的目錄是{metadata['path']}')
    output=processing(metadata)
    
    if output == 0: 
        print('無資料可用或使用者取消...')
        
    else:
        ct_database=backup_sql(output)
        export_csv(ct_database, metadata)
        
                
except:
    print('metadata.json讀取失敗或不存在')
    print('重新創造metadata.json....')
    print('若無法讀取，請修改metadata.json中path裡面所要讀取的DICOM母路徑')
    
    #預設值
    metadata: dict= {'說明1':'path為路徑名稱，有預設值，若為"\"符號請重複兩次。',
                     '說明2':'last_reading_date為最後讀取日期，當執行時候必為當日，不用更改。',
                     '說明3' : 'omit_year為忽略年分，請在方括號裡面填入不要輸出的年份並用,號分開',
                    'path':'\\\\Aria15sql\\dimcom\\CT_DOSE', 'last_reading_date':str(today), 'omit_year':'[]'}
    with open("metadata.json", "w", encoding="utf-8") as json_file:
        json.dump(metadata, json_file, ensure_ascii=False, indent=4)

input('按Enter鍵結束..')
