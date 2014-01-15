# extrinsic_testing.py

import xml
import random
import scipy
import sklearn
import sklearn.metrics
import matplotlib
matplotlib.use('pdf')
import matplotlib.pyplot as pyplot
import os
import time
import nltk
import fingerprint_extraction
import extrinsic_processing
import ground_truth
from ..shared.util import ExtrinsicUtility

import sqlalchemy
from sqlalchemy import Table, Column, Sequence, Integer, String, Float, DateTime, ForeignKey, and_
from sqlalchemy.orm import relationship, backref, sessionmaker
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm.collections import attribute_mapped_collection
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.associationproxy import association_proxy

class ExtrinsicTester:

    def __init__(self, atom_type, fingerprint_method, n, k, suspect_file_list, source_file_list):
        self.suspicious_path_start = ExtrinsicUtility.CORPUS_SUSPECT_LOC
        self.corpus_path_start = ExtrinsicUtility.CORPUS_SRC_LOC
        source_dirs = os.listdir(self.corpus_path_start)
            
        self.atom_type = atom_type
        self.fingerprint_method = fingerprint_method
        self.n = n
        self.k = k
        self.suspect_file_list = suspect_file_list
        self.source_file_list = source_file_list
        self.evaluator = fingerprint_extraction.FingerprintEvaluator(source_file_list, fingerprint_method, self.n, self.k)

    def _get_trials(self):
        '''
        For each testing document, split the document into atoms and classify each atom
        as plagiarized or not-plagiarized. Build a list of classifications and a list
        of the ground truths for each atom of each document.
        '''
        classifications = []
        actuals = []
        for f in self.suspect_file_list:
            suspicious_document = open(f + '.txt')
            doc = suspicious_document.read()
            
            suspicious_document.close()
            doc_name = f.replace(self.suspicious_path_start, "")

            acts = ground_truth._query_ground_truth(f, self.atom_type, session, self.suspicious_path_start).get_ground_truth(session)
            actuals += acts

            print f
            for i in xrange(len(acts)):
                print 'Classifying', doc_name
                atom_classifications = self.evaluator.classify_document(doc_name, self.atom_type, i, self.fingerprint_method, self.n, self.k, session)
                # just take the most similar source document's similarity as the confidence of plagiarism for now.
                classifications.append(atom_classifications[0][1])
                print 'atom index:', str(i+1) + '/' + str(len(acts))
                print 'confidence (actual, guess):', acts[i], atom_classifications[0][1]

        return classifications, actuals

    def plot_ROC_curve(self):
        '''
        Outputs an ROC figure based on our plagiarism classifications and the 
        ground truth of each atom.
        '''
        trials, actuals = self._get_trials()

        # actuals is a list of ground truth classifications for passages
        # trials is a list consisting of 0s and 1s. 1 means we think the atom is plagiarized
        fpr, tpr, thresholds = sklearn.metrics.roc_curve(actuals, trials, pos_label=1)
        roc_auc = sklearn.metrics.auc(fpr, tpr)

        # The following code is from http://scikit-learn.org/stable/auto_examples/plot_roc.html
        pyplot.clf()
        pyplot.plot(fpr, tpr, label='ROC curve (area = %0.2f)' % roc_auc)
        pyplot.plot([0, 1], [0, 1], 'k--')
        pyplot.xlim([0.0, 1.0])
        pyplot.ylim([0.0, 1.0])
        pyplot.xlabel('False Positive Rate')
        pyplot.ylabel('True Positive Rate')
        pyplot.title('Receiver Operating Characteristic -- Extrinsic w/ '+self.fingerprint_method+' fingerprinting')
        pyplot.legend(loc="lower right")
        
        path = os.path.join(os.path.dirname(__file__), "../figures/roc_extrinsic_"+str(time.time())+"_"+self.fingerprint_method+".pdf")
        pyplot.savefig(path)


if __name__ == "__main__":
    session = extrinsic_processing.Session()
    # fp = extrinsic_processing._query_fingerprint('/part7/suspicious-document12675', "full", 3, 5, "paragraph", session, '/copyCats/pan-plagiarism-corpus-2009/external-detection-corpus/suspicious-documents')
    # print fp.get_fingerprints(session)
    
    util = ExtrinsicUtility()
    num_files = 1

    source_file_list, suspect_file_list = util.get_n_training_files(n=num_files, include_txt_extension=False)

    print 'Testing first', num_files, ' suspect files using a corpus of', len(source_file_list), 'source documents:'
    print 'Suspect filenames:', suspect_file_list

    atom_type = "paragraph"
    method = "kth_in_sent"
    n = 3
    k = 5

    tester = ExtrinsicTester(atom_type, method, n, k, suspect_file_list, source_file_list)
    tester.plot_ROC_curve()

    # tester = ExtrinsicTester("paragraph", "full", suspect_file_list, source_file_list)
    # tester.plot_ROC_curve()

    # tester = ExtrinsicTester("paragraph", "kth_in_sent", suspect_file_list, source_file_list)
    # tester.plot_ROC_curve()

    # tester = ExtrinsicTester("paragraph", "anchor", suspect_file_list, source_file_list)
    # tester.plot_ROC_curve()
