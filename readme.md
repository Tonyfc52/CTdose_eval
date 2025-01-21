# CT dose secondary capture collection program
## This program just for GE CT simulator

Version 1.0

2025/01/21

此程式只針對放射治療用的GE CT simulator的secondary capture格式

其他機器需針對不同SC DICOM檔案改寫

### 系統執行說明

### Step1:執行
此程式針對已經上傳到dicom server 各子目錄的SC-xxxxxxxxx.dcm做收集並解析，所以在metadata.json (若一開始此檔案不存在，會先建立起來，重新執行第二次便會依照預設值執行)中，path為**dicom server母目錄**的指向

* 註：路徑上若有 \ 符號，需重覆成 \\\，不過程式在找不到路徑時，會開始視窗提供選擇母目錄的路徑，若正確執行後，會將結果儲存在metadata.json中，例如
  
```
"path": "\\\\sql\\dicom\\CTDOSE"
```

### Step2:備份
找到子目錄裡面的SC-xxxxxxx.dcm後，系統會解析裡面的內容。接著會備份到同目錄中的CTdose_eval.db中 (Sqlite3格式)

### Step3:輸出
接著系統會將db中的資料進一步照年份處理，會照年份跟部位輸出到子目錄output成.csv檔案以便編輯

* 隨著年份增加，可能會有不想輸出的年份。在metadata.json中有omit_year欄位。可將不需要的年份填入在方括號中。例如
  
```
"omit_year": "[2025, 2026]"
```
* 系統載入時，會比對資料庫的年份，若有符合的，會將omit_year裡面的年份不予輸出。


### 其他
**目前版本不會刪除dicom server的.dcm檔案**，請過一段時間執行此程式備份結束後，再刪除dicom server的.dcm檔案
