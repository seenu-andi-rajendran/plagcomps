class Trial:

    @staticmethod
    def format_header(all_possible_features):
        '''
        The header for any trial (which is why it's static)
        '''
        other_columns = ['docname', 'true_pos', 'false_pos', 'true_neg', 'false_neg']
        header = ', '.join(all_possible_features + other_columns) + '\n'

        return header

    def __init__(self, docname, features, true_positives, false_positives, true_negatives, false_negatives):
        self.docname = docname
        self.features = features
        self.true_positives = true_positives
        self.false_positives = false_positives
        self.true_negatives = true_negatives
        self.false_negatives = false_negatives
        self.num_correct = self.true_positives + self.true_negatives
        self.total = self.true_positives + self.false_positives + self.true_negatives + self.false_negatives
        
    def format_csv(self, all_possible_features):
        '''
        Returns a csv representation of this trial. If <all_possible_features> has 
        <n> features, the first <n> fields are binary indicators (1 if the
        feature was used in this trial). The following 3 fields are the document's 
        name, the number of correctly classified passages, and the total number
        of passages
        '''
        # feature_present_vec[i] = 0 iff all_possible_features[i] 
        # was used in this trial; 1 otherwise
        feature_present_vec = ['1' if f in self.features else '0' for f in all_possible_features ]

        docname_relative = self.get_file_part_and_name(self.docname)
        other_columns = [
            docname_relative, 
            str(self.true_positives),
            str(self.false_positives),
            str(self.true_negatives),
            str(self.false_negatives)
        ]
        
        # Gives a csv representation of this trial
        return ', '.join(feature_present_vec + other_columns) + '\n'




    def get_file_part_and_name(self, full_path):
        '''
        Strips off the end of the file name, relative to the main 
        corpus directory
        '''
        # TODO write this. Output would be nicer if it wasn't the full path
        # to the file used in this trial
        suffix_index = full_path.index('/part')

        return full_path[suffix_index:]

    def get_pct_correct(self):
        '''
        Return the number of correctly classfied passages over the total number of passages
        '''
        return float(self.num_correct) / self.total
    
    def get_percision(self):
        '''Return the number of true positives over the number of true postives plus false positives'''
        return float(self.true_positives) / (self.true_positives + self.false_positives)
    
    def get_recall(self):
        '''
        Return the number of true positives over the number of true positives plus false negatives.
        '''
        return float(self.true_positives) / (self.true_positives + self.false_negatives)

