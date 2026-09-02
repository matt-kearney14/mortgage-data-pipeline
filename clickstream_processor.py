import pandas as pd

# ==========================================
# MASTER VARIABLES (adjust these to match the raw data file's column names)
# ==========================================
USER_COL = 'user_hash'
TIME_COL = 'eventdate'
URL_COL  = 'path'
# ==========================================

class ClickstreamPath:
    def __init__(self, raw_data_file, mapping_file):
        """
        Initializes by reading both your raw user clicks AND your Excel data dictionary.
        """

        #raw_data_file load status:
        self.df = pd.read_excel(raw_data_file) #adjust read type depending on "csv" or "xlsx" here
        self.df[TIME_COL] = pd.to_datetime(self.df[TIME_COL])
        self.df = self.df.sort_values(by=[USER_COL, TIME_COL]).reset_index(drop=True)
        
        #mapping file load status:
        self.mapping_df = pd.read_excel(mapping_file, sheet_name='beta_coding')

    def apply_path_logic(self):
        """Processes Variables 1 through 21 using the mapping file and dynamic rules."""
        
        #match "raw_data_file" to "mapping file" column names 
        self.df = self.df.merge(self.mapping_df, left_on=URL_COL, right_on='CODING SCHEME', how='left')
        
        #fill missing values with 0 (for values)
        static_vars = [
            'Personalized', 'Download', 'LoanEstimateRelated', 'LoanTermsRelated',
            'LenderMortgageProcessRelated', 'GeneralFinancial', 'Video', 'CDRelated',
            'Goal_to_Advise', 'ProcessRelated', 'BorrowerMortgageProcessRelated',
            'Goal_to_inform', 'MortgageRelated', 'Audio'
        ]
        self.df[static_vars] = self.df[static_vars].fillna(0).astype(int)

        #Simple variable parsing (doesn't run via match/merge process above)
        self.df['English_YN'] = (~self.df[URL_COL].str.contains('/translations/es', na=False)).astype(int)
        self.df['LEDocument'] = self.df[URL_COL].str.contains(r'/Download/LoanDocument/.*LE', regex=True, na=False).astype(int)
        self.df['CDDocument'] = self.df[URL_COL].str.contains(r'/Download/LoanDocument/.*CD', regex=True, na=False).astype(int)
        self.df['AudioMp3'] = self.df[URL_COL].str.endswith('.mp3', na=False).astype(int)
        self.df['Spanish_YN'] = self.df[URL_COL].str.contains('/translations/es', na=False).astype(int)
        
        #Sequential variable parsing (checking user's previous action)
        self.df['prev_LoanEstimateRelated'] = self.df.groupby(USER_COL)['LoanEstimateRelated'].shift(1)
        self.df['LEDownload'] = ((self.df['Download'] == 1) & (self.df['prev_LoanEstimateRelated'] == 1)).astype(int)
        
        self.df['prev_CDRelated'] = self.df.groupby(USER_COL)['CDRelated'].shift(1) 
        self.df['CDDownload'] = ((self.df['Download'] == 1) & (self.df['prev_CDRelated'] == 1)).astype(int)
        
        #delete temp tracking columns 
        self.df = self.df.drop(columns=['prev_LoanEstimateRelated', 'prev_CDRelated'], errors='ignore')
        
        return self.df

if __name__ == "__main__": #pointing to mapping scheme
    engine = ClickstreamPath(
        raw_data_file="talkument_userinteractions.xlsx", #insert data file here
        mapping_file="Clickstream_path_frequencies_and_coding_scheme.xlsx" #automatic mapping file (DO NOT TOUCH)
    )
    
    final_data = engine.apply_path_logic()

    #export status
    final_data.to_excel("Processed_URL_Variables_1_to_21.xlsx", index=False)
    print("Data mapped successfully using the Excel dictionary!")