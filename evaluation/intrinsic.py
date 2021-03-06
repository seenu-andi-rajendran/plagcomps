from ..intrinsic.featureextraction import FeatureExtractor
from ..shared.util import BaseUtility, IntrinsicUtility, ExtrinsicUtility
from ..dbconstants import username
from ..dbconstants import password
from ..dbconstants import dbname
from plagcomps.intrinsic.cluster import cluster
from ..tokenization import tokenize
from plagcomps.evaluation.precrecall import prec_recall_evaluate

import datetime
import numpy.random
import xml.etree.ElementTree as ET
import time
import codecs
import itertools
from os import path as ospath

import sklearn.metrics
import matplotlib
matplotlib.use('pdf')
import matplotlib.pyplot as pyplot

import sqlalchemy
from sqlalchemy import Table, Column, Sequence, Integer, String, Float, DateTime, ForeignKey, and_
from sqlalchemy.orm import relationship, backref, sessionmaker
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm.collections import attribute_mapped_collection
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.associationproxy import association_proxy

import scipy, random

Base = declarative_base()

DEBUG = True
DB_VERSION_NUMBER = 5


def populate_database(atom_types, num, features=None, corpus='intrinsic', corpus_type='training'):
    '''
    Populate the database with the first num training files parsed with the given atom_types.
    Uses the features passed as an optional parameter, or all of them.
    Refer to the code to see which features it populates when no features passed.
    '''
    session = Session()
    
    # Get the first num training files
    if corpus == 'intrinsic':
        util = IntrinsicUtility()
        first_training_files = util.get_n_training_files(num, corpus_type=corpus_type)
    elif corpus == 'extrinsic':
        util = ExtrinsicUtility()
        first_training_files = util.get_corpus_files(n=num, path_type="absolute", file_type='suspect', include_txt_extension=True)
    else:
        raise Exception("Invalid corpus specified: " + str(corpus))
        
    print first_training_files

    if features == None:
        features = FeatureExtractor.get_all_feature_function_names(include_nested=True)
        
    i = 0
    for doc in first_training_files:
        print doc
        i += 1
        for atom_type in atom_types:
            d = _get_reduced_docs(atom_type, [doc], session, corpus=corpus)[0]
            for feature in features:
                if 'evolved' not in feature:
                    print "Doc num " + str(i) + "/" + str(len(first_training_files)) + " Calculating", feature, "for", str(d), str(datetime.datetime.now())
                    d.get_feature_vectors([feature], session)

    session.close()

def evaluate_n_documents(features, cluster_type, k, atom_type, n, eval_method = 'roc', corpus='intrinsic', save_roc_figure=True, min_len=None,
                        first_doc_num=0, feature_weights=None, feature_confidence_weights=None, cheating=False, cheating_min_len=5000, corpus_type='training'):
    '''
    Return the evaluation (roc curve path, area under the roc curve) of the first n training
    documents parsed by atom_type, using the given features, cluster_type, and number of clusters k.

    <min_len> is the minimum number of characters a document must contain
    in order to be included. If None, then all documents are considered
    '''
    # Get the first n training files
    # NOTE (nj) pass keyword arg min_len=35000 (or some length) in order to 
    # get <n> files which all contain at least 35000 (or some length) characters, like:
    # first_training_files = IntrinsicUtility().get_n_training_files(n, min_len=35000)
    # as is done in Stein's paper
    first_training_files = IntrinsicUtility().get_n_training_files(n, min_len=min_len, first_doc_num=first_doc_num, corpus_type=corpus_type)
    # Also returns reduced_docs from <first_training_files>
    metadata = {
        'min_len' : min_len,
        'first_doc_num' : first_doc_num
    }

    # (nj) in order to keep DB logic out of precrecall.py, create the session
    # and grab the reduced docs first and pass them
    session = Session()
    reduced_docs = _get_reduced_docs(atom_type, first_training_files, session, corpus=corpus)

    roc_path, roc_auc = \
        evaluate(reduced_docs, session, features, cluster_type, k, atom_type, first_training_files, corpus=corpus, save_roc_figure=save_roc_figure, 
                                    feature_vector_weights=feature_weights, feature_confidence_weights=feature_confidence_weights, metadata=metadata,
                                    cheating=cheating, cheating_min_len=cheating_min_len)

    thresh_prec_avgs, thresh_recall_avgs, thresh_fmeasure_avgs, thresh_granularity_avgs, thresh_overall_avgs = \
            prec_recall_evaluate(reduced_docs, session, features, cluster_type, k, 
                atom_type, corpus=corpus, feature_vector_weights=feature_weights, 
                metadata=metadata, cheating=cheating, cheating_min_len=cheating_min_len)

    print '-----'
    print 'features:', features
    print 'cluster_type:', cluster_type
    print 'atom_type:', atom_type
    print 'n:', n
    
    session.close()

    return roc_path, roc_auc, thresh_prec_avgs, thresh_recall_avgs, thresh_fmeasure_avgs, thresh_granularity_avgs, thresh_overall_avgs


def evaluate(reduced_docs, session, features, cluster_type, k, atom_type, docs, corpus='intrinsic', save_roc_figure=True, feature_vector_weights=None, 
            metadata={}, cheating=False, cheating_min_len=5000, **clusterargs):
    '''
    Return the roc curve path and area under the roc curve for the given list of documents parsed
    by atom_type, using the given features, cluster_type, and number of clusters k.
    
    features is a list of strings where each string is the name of a StylometricFeatureEvaluator method.
    cluster_type is "kmeans", "hmm", or "agglom".
    k is an integer.
    atom_type is "word", "sentence", or "paragraph".
    docs should be a list of full path strings.
    '''
    # If previous call cached <reduced_docs>, don't re-query the DB
    plag_likelihoods = []
    doc_plag_assignments = {}
    
    count = 0
    valid_reduced_docs = []
    for d in reduced_docs:
        count += 1
        if DEBUG:
            print "On document", d, ". The", count, "th document."

        feature_vecs = d.get_feature_vectors(features, session, cheating=cheating, cheating_min_len=cheating_min_len)
        # skip if there are no feature_vectors
        if cheating and len(feature_vecs) < 7: # 7, because that's what Benno did
            continue
        valid_reduced_docs.append(d)

        if feature_vector_weights:
            weighted_vecs = []
            for vec in feature_vecs:
                cur_weight_vec = []
                for i, weight in enumerate(feature_vector_weights, 0):
                    cur_weight_vec.append(vec[i] * weight)
                weighted_vecs.append(cur_weight_vec)
            feature_vecs = weighted_vecs

        likelihood = cluster(cluster_type, k, feature_vecs, **clusterargs)
        doc_plag_assignments[d] = likelihood
        plag_likelihoods.append(likelihood)
    
    metadata['features'] = features
    metadata['cluster_type'] = cluster_type
    metadata['k'] = k
    metadata['atom_type'] = atom_type
    metadata['n'] = len(reduced_docs)
    roc_path, roc_auc = _roc(valid_reduced_docs, plag_likelihoods, save_roc_figure=save_roc_figure, cheating=cheating, cheating_min_len=cheating_min_len, **metadata)

    # Return reduced_docs for caching in case we call <evaluate> multiple times
    return roc_path, roc_auc

def get_confidences_actuals(session, features, cluster_type, k, atom_type, docs, corpus='intrinsic', save_roc_figure=True, reduced_docs=None, feature_vector_weights=None, metadata={}, cheating=False, cheating_min_len=5000, **clusterargs):
    '''
    Return the confidences and acutals for the given list of documents parsed
    by atom_type, using the given features, cluster_type, and number of clusters k.
    
    features is a list of strings where each string is the name of a StylometricFeatureEvaluator method.
    cluster_type is "kmeans", "hmm", or "agglom".
    k is an integer.
    atom_type is "word", "sentence", or "paragraph".
    docs should be a list of full path strings.
    '''
    # TODO: Return more statistics, not just roc curve things.
    # If previous call cached <reduced_docs>, don't re-query the DB
    if not reduced_docs:
        reduced_docs = _get_reduced_docs(atom_type, docs, session, corpus=corpus)
    plag_likelihoods = []
    actuals = []
    
    count = 0
    valid_reduced_docs = []
    for d in reduced_docs:
        count += 1
        if DEBUG:
            print "On document", d, ". The", count, "th document."

        feature_vecs = d.get_feature_vectors(features, session, cheating=cheating, cheating_min_len=cheating_min_len)
        # skip if there are no feature_vectors
        if cheating and len(feature_vecs) < 7: # 7, because that's what Benno did
            continue
        valid_reduced_docs.append(d)

       # add to actuals
        spans = d.get_spans()
        for i in xrange(len(spans)):
            span = spans[i]
            actuals.append(1 if d.span_is_plagiarized(span) else 0)
 
        if feature_vector_weights:
            weighted_vecs = []
            for vec in feature_vecs:
                cur_weight_vec = []
                for i, weight in enumerate(feature_vector_weights, 0):
                    cur_weight_vec.append(vec[i] * weight)
                weighted_vecs.append(cur_weight_vec)
            feature_vecs = weighted_vecs

        likelihood = cluster(cluster_type, k, feature_vecs, **clusterargs)
        plag_likelihoods.append(likelihood)
    
    session.close()

    all_confidences = []
    for likelihood_list in plag_likelihoods:
        all_confidences += likelihood_list

    return all_confidences, actuals

    
def _output_to_file(assignment_dict, atom_type, cluster_type):

    for document in assignment_dict.keys():
        output_file = open(ospath.join(ospath.dirname(__file__), "../intrinsic/textAnalysis/" + str(time.time()) + ".txt"), "w")
        plag_atoms = []
        non_plag_atoms = []
        for i in xrange(len(assignment_dict[document])):
            if assignment_dict[document][i] <= 0.5:
                non_plag_atoms.append(i)
            else:
                plag_atoms.append(i)

        reader = open(document.full_path)
        text = reader.read()
        reader.close()

    	total_atoms = len(non_plag_atoms) + len(plag_atoms)
        atom_spans = tokenize(text, atom_type)

    	output_file.write("Document Name: " +  document._short_name + "\n")
    	output_file.write("Atom_Type: " + atom_type + "\n")
    	output_file.write("Cluster_Method: " + cluster_type + "\n")
    	output_file.write("Plagiarized Atoms Count: " + str(len(plag_atoms)) + "/" + str(total_atoms) + "\n")
    	output_file.write("Non-Plagiarized Atoms Count: " + str(len(non_plag_atoms)) + "/" + str(total_atoms) + "\n\n")
    	output_file.write("---"*25 + "\n")
    	output_file.write("NON-PLAGIARIZED ATOMS\n")
    	output_file.write("---"*25 + "\n")

    	for index in non_plag_atoms:
    		atom_text = text[atom_spans[index][0]:atom_spans[index][1]]
    		output_file.write("This is an atom: \n")
    		output_file.write(atom_text + "\n\n")

    	output_file.write("---"*25 + "\n")
    	output_file.write("PLAGIARIZED ATOMS\n")
    	output_file.write("---"*25 + "\n")

    	for index in plag_atoms:
    		atom_text = text[atom_spans[index][0]:atom_spans[index][1]]
    		output_file.write("This is an atom: \n")
    		output_file.write(atom_text + "\n\n")

    	output_file.write("***"*25)
    	output_file.close()
		

def compare_outlier_params(n, features=None, min_len=None):
    '''
    Tries a number of combinations of parameters for outlier classification,
    namely different atom_types, methods of centering distributions, and number
    of extreme points to ignore.

    Runs on <n> documents using all features unless <features> is a provided
    argument. 

    Only run on documents of length at least <min_len>, or all documents
    if <min_len> is not a provided argument
    '''
    atom_types = ['paragraph', 'sentence']
    center_at_mean = [True, False]

    docs = IntrinsicUtility().get_n_training_files(n, min_len=min_len)
    if not features:
        features = FeatureExtractor.get_all_feature_function_names()

    results = []

    
    for atom_type in atom_types:
        for c in center_at_mean:
            # NOTE (nj) this will break now, but we don't care since we've
            # already set our outlier params
            roc_path, roc_auc = \
                evaluate(features, 'outlier', 2, atom_type, docs, center_at_mean=c)

            one_trial = (atom_type, min_len, c, roc_path, roc_auc)
            results.append(one_trial)
            print one_trial
            print '-'*30

    for r in results:
        print r
    return results

def compare_cluster_methods(feature, n, cluster_types):
    '''
    Generates a plot that displays ROC curves based on the first n documents and the given
    feature. Creates an ROC curve for each of the cluster methods in cluster_types.
    cluster_types should be a list of tuples like (function_obj, label, (argument list)).
    argument list should NOT include the final argument, which is assumed to be feature
    vectors.
    So, one might call the function as follows:
       compare_cluster_methods("average_word_length", 100, [(cluster, "2means", ("kmeans,"2))])
    '''
    
    # Get the reduced_docs
    docs = IntrinsicUtility().get_n_training_files(n)
    session = Session()
    reduced_docs = _get_reduced_docs("paragraph", docs, session)
    
    # Prepare to plot
    pyplot.clf()
    
    # plot a curve for each clustering strategy
    for method in cluster_types:
        func = method[0]
        label = method[1]
        
        # build confidences and actuals
        confidences = []
        actuals = []
        for d in reduced_docs:
            # add to confidences
            args = method[2] + (d.get_feature_vectors([feature], session),)
            passage_confidences = func(*args)
            for c in passage_confidences:
                confidences.append(c)
            # add to actuals
            spans = d.get_spans()
            for i in xrange(len(spans)):
                span = spans[i]
                actuals.append(1 if d.span_is_plagiarized(span) else 0)
        
        # Calculate the fpr and tpr
       # Outlier breaks here.
        print "----"*20
        print "method: ", method
        print "fewature", feature       
        print "----"*20
        fpr, tpr, thresholds = sklearn.metrics.roc_curve(actuals, confidences, pos_label=1)
        roc_auc = sklearn.metrics.auc(fpr, tpr)
        pyplot.plot(fpr, tpr, label='%s (area = %0.2f)' % (label, roc_auc))
    
    # plot labels and such
    pyplot.plot([0, 1], [0, 1], 'k--')
    pyplot.xlim([0.0, 1.0])
    pyplot.ylim([0.0, 1.0])
    pyplot.xlabel('False Positive Rate')
    pyplot.ylabel('True Positive Rate')
    pyplot.title(feature + ", " + str(n) + " docs") 
    pyplot.legend(loc="lower right")
    
    path = ospath.join(ospath.dirname(__file__), "../figures/clust_comp_"+feature+"_"+str(time.time())+".pdf")
    pyplot.savefig(path)
    
    session.close()
    
    return path

def _stats_evaluate_n_documents(features, atom_type, n):
    '''
    Does _feature_stats_evaluate(features, atom_type, doc) on the first n docuemtns of the training
    set. Returns None.
    '''
    first_training_files = IntrinsicUtility().get_n_training_files(n)
    return _feature_stats_evaluate(features, atom_type, first_training_files)

def _feature_stats_evaluate(features, atom_type, docs):
    '''
    This function writes data to two files. The data is used by an R script to generate box plots
    that show the significance of the given features within the given documents. Returns None.
    '''
    session = Session()
    
    document_dict = {}
    percent_paragraphs = .10
    same_doc_outfile = open('test_features_on_self.txt', 'w')
    different_doc_outfile = open('test_features_on_different.txt', 'w')

    reduced_docs = _get_reduced_docs(atom_type, docs, session)
    for d in reduced_docs:
        document_dict[d._short_name] = {}

        paragraph_feature_list = d.get_feature_vectors(features, session)
        spans = d.get_spans()
        
        # builds the dictionary for feature vectors for each non-plagiarized paragraph in each document        
        for paragraph_index in range(len(paragraph_feature_list)):
            if not d.span_is_plagiarized(spans[paragraph_index]):
                for feature_index in range(len(features)):
                    # ask zach/noah what this line does
                    document_dict[d._short_name][features[feature_index]] = document_dict[d._short_name].get(features[feature_index], [])+ [paragraph_feature_list[paragraph_index][feature_index]]

    for feature in features:
        for doc in document_dict:    
            # take two samples and compare them for our same_doc test, then pick one of the samples and save it for our later tests
            feature_vect = document_dict[doc][feature] 
            sample = random.sample(feature_vect, max(2, int(len(feature_vect)*percent_paragraphs*2)))
            sample_one = sample[:len(sample)/2]
            sample_two = sample[len(sample)/2:]
            print "SAMPLES:", sample_one, sample_two
            stats = scipy.stats.ttest_ind(sample_one, sample_two)
            same_doc_outfile.write(feature + "\t" + doc + "\t" + str(stats[1]) + "\r\n")
            document_dict[doc][feature] = sample_one

    for feature in features:
        for first in document_dict: 
            for second in document_dict:
                 if first != second:
                     sample_one = document_dict[first][feature]
                     sample_two = document_dict[second][feature]
                     stats = scipy.stats.ttest_ind(sample_one, sample_two)
                     different_doc_outfile.write(feature + "\t" + first + "," + second + "\t" + str(stats[1]) + "\r\n") 

    same_doc_outfile.close()
    different_doc_outfile.close()
    session.close() 

def _get_reduced_docs(atom_type, docs, session, corpus='intrinsic', create_new=True):
    '''
    Return ReducedDoc objects for the given atom_type for each of the documents in docs. docs is a
    list of strings like "/copyCats/pan-plagiarism-corpus-2009/intrinsic-detection-corpus/suspicious-documents/part2/suspicious-document02456.txt".
    They must be full paths. This function retrieves the corresponding ReducedDocs from
    the database if they exist. 

    Otherwise, if <create_new> is True (which it is by default), it creates new ReducedDoc objects.
    Set <create_new> to False if you don't want to wait for new ReducedDoc objects to be created.
    Useful if just messing around from the command line/doing sanity checks 
    '''
    reduced_docs = []
    for doc in docs:
        try:
            r = session.query(ReducedDoc).filter(and_(ReducedDoc.full_path == doc, ReducedDoc.atom_type == atom_type, ReducedDoc.version_number == DB_VERSION_NUMBER)).one()
        except sqlalchemy.orm.exc.NoResultFound, e:
            if create_new:
                r = ReducedDoc(doc, atom_type, corpus)
                session.add(r)
                session.commit()
            else:
                continue
        except sqlalchemy.orm.exc.MultipleResultsFound, e:
            # This is funky.
            print "========="
            print "WE HAVE A PROBLEM! THERE ARE MULTIPLE COPIES OF A REDUCED DOC!"
            print "========="
            r = session.query(ReducedDoc).filter(and_(ReducedDoc.full_path == doc, ReducedDoc.atom_type == atom_type, ReducedDoc.version_number == DB_VERSION_NUMBER)).first()
        reduced_docs.append(r)
        
    return reduced_docs

def _roc(reduced_docs, plag_likelihoods, save_roc_figure=True, cheating=False, cheating_min_len=5000, **metadata):
    '''
    Generates a reciever operator characterstic (roc) curve and returns both the path to a pdf
    containing a plot of this curve and the area under the curve. reduced_docs is a list of
    ReducedDocs, plag_likelihoods is a list of lists whrere plag_likelihoods[i][j] corresponds
    to the likelihood that the jth span in the ith reduced_doc was plagiarized.
    
    The optional parameters are written to a JSON file that stores metadata about the curve.
    Suggested parameters are:
    features, cluster_type, k, atom_type
    
    Note that all passages have equal weight for this curve. So, if one document is considerably
    longer than the others and our tool does especially poorly on this document, it will look as
    if the tool is bad (which is not necessarily a bad thing).
    '''    
    # This function was modeled in part from this example:
    # http://scikit-learn.org/0.13/auto_examples/plot_roc.html
    
    actuals = []
    confidences = []

    for doc_index in xrange(len(reduced_docs)):
        doc = reduced_docs[doc_index]
        spans = doc.get_spans(cheating, cheating_min_len)
        if cheating and not len(spans):
            continue
        for span_index in xrange(len(spans)):
            span = spans[span_index]
            actuals.append(1 if doc.span_is_plagiarized(span) else 0)
            confidences.append(plag_likelihoods[doc_index][span_index])
    print reduced_docs
    print actuals
    print confidences
    # actuals is a list of ground truth classifications for passages
    # confidences is a list of confidence scores for passages
    # So, if confidences[i] = .3 and actuals[i] = 1 then passage i is plagiarized and
    # we are .3 certain that it is plagiarism (So its in the non-plag cluster).

    # metadata generally also includes keys: features, cluster_type, k, atom_type
    metadata['n'] = len(reduced_docs)    
    path, roc_auc = BaseUtility.draw_roc(actuals, confidences, save_figure=save_roc_figure, **metadata)

    return path, roc_auc

class ReducedDoc(Base):
    '''
    The class represents a suspect document that has been reduced to passages and feature
    vectors. Features are extracted lazily. 
    '''
    
    __tablename__ = "reduced_doc"
    
    id = Column(Integer, Sequence("reduced_doc_id_seq"), primary_key=True)
    _short_name = Column(String)
    full_path = Column(String)
    _full_xml_path = Column(String)
    atom_type = Column(String)
    _spans = Column(ARRAY(Integer))
    _plagiarized_spans = Column(ARRAY(Integer))

    # 'intrinsic' or 'extrinsic'
    corpus = Column(String)

    # The miracle of features is explained here:
    # http://docs.sqlalchemy.org/en/rel_0_7/orm/extensions/associationproxy.html#proxying-dictionaries
    _features = association_proxy(
                    'reduced_doc_features',
                    'feature',
                    creator=lambda k, v:
                        _ReducedDocFeature(special_key=k, feature=v)
                )
                
    timestamp = Column(DateTime)
    version_number = Column(Integer)
    
    def __init__(self, path, atom_type, corpus):
        '''
        Initializes a ReducedDoc. No feature vectors will be calculated at instantiation time.
        get_feature_vectors triggers the lazy instantiation of these values.
        '''
        if '.txt' in path or '.xml' in path:
            path = path[:-4]
        
        self.full_path = path + ".txt"

        # _short_name example: '/part1/suspicious-document00536'
        self._short_name = "/"+self.full_path.split("/")[-2] +"/"+ self.full_path.split("/")[-1] 

        self._full_xml_path = path + ".xml"

        # 'intrinsic' or 'extrinsic'
        self.corpus = corpus
        
        self.atom_type = atom_type
        self.timestamp = datetime.datetime.now()
        self.version_number = DB_VERSION_NUMBER
        
        # NOTE: I think the note below is outdated/different now.
        #       Now FeatureExtractor.get_feature_vectors() does return a feature for each
        #       span. So, we could set self._spans now. But, the initialization of that
        #       object does take same time, so we would want to save it probably rather
        #       than do it twice.
        # NOTE: because feature_evaluator.get_specific_features doesn't actually return a
        # passage object for each span, we can't set self._spans until that has been run.
        # I don't like it though, because we have to raise an error now if self.get_spans()
        # is called before any feature_vectors have been calculated.
        
        # set self._spans
        #f = open(self.full_path, 'r')
        #text = f.read()
        #f.close()
        #self._spans = FeatureExtractor(text).get_spans(self.atom_type)
        self._spans = None
        
        # set self._plagiarized_spans
        self._plagiarized_spans = IntrinsicUtility().get_plagiarized_spans(self._full_xml_path)
    
    def __repr__(self):
        return "<ReducedDoc('%s','%s', '%s')>" % (self._short_name, self.atom_type, self.corpus)
    
    def get_feature_vectors(self, features, session, cheating=False, cheating_min_len=5000):
        '''
        Returns a list of feature vectors for each passage in the document. The components
        of the feature vectors are in order of the features list.
        '''
        feature_vectors = zip(*[self._get_feature_values(x, session) for x in features])
        if not cheating:
            return feature_vectors

        cheating_feature_vectors = []
        cheating_spans = []
        # only return feature vectors part of a 'cheating' span

        for feature_vector, span in zip(feature_vectors, self.get_spans(cheating=False)):
            if not self.cheating_excludes_span(span, cheating_min_len):
                cheating_feature_vectors.append(feature_vector)
                cheating_spans.append(span)
        return cheating_feature_vectors
    

    def get_spans(self, cheating=False, cheating_min_len=5000):
        '''
        Returns a list of lists. The ith list contains the start and end characters for the ith
        passage in this document.
        '''
        if self._spans == None:
            raise Exception("See note in ReducedDoc.__init__")
        else:
            if not cheating:
                return self._spans
            else:
                return [s for s in self._spans if not self.cheating_excludes_span(s, cheating_min_len)]

    def get_plag_spans(self):
        '''
        Returns list of lists, where the ith lisst contains the start and end characters for
        the ith PLAGIARIZED pasage in this document
        '''
        return self._plagiarized_spans

    def cheating_excludes_span(self, span, min_chars):
        '''
        Returns True if the given span should be excluded when 'cheating'.
        '''
        if span[1] - span[0] < min_chars:
            return True

        overlap_amount = 0
        for s in self._plagiarized_spans:
            overlap_amount += BaseUtility().overlap(span, s)

        if overlap_amount < (span[1] - span[0])/2.0 and overlap_amount > 0:
            return True
        return False
    
    def span_is_plagiarized(self, span, cheating=False):
        '''
        Returns True if the span was plagiarized (according to the ground truth). Returns
        False otherwise. A span is considered plagiarized if it overlaps a plagiarised span.
        
        '''
        # TODO: Consider other ways to judge if an atom is plagiarized or not. 
        #       For example, look to see if the WHOLE atom in a plagiarized segment (?)        
        for s in self._plagiarized_spans:
            if not cheating:
                if BaseUtility().overlap(span, s) > 0:
                    return True
            else:
                if BaseUtility().overlap(span, s) > (span[1] - span[0])/2.0:
                    return True
        return False
        
    def _get_feature_values(self, feature, session, populate = True):
        '''
        Returns the list of feature values for the given feature, instantiating them if
        need be. If populate is False, feature values will not be instantiatiated.
        '''
        try:
            return self._features[feature]
            
        except KeyError:
            if populate == False:
                raise KeyError()
    
            # Read the file
            f = open(self.full_path, 'r')
            text = f.read()
            # Corpus docs have a BOM_UTF8 at the start -- let's strip those
            # 3 characters. Suggestion comes from:
            # http://stackoverflow.com/questions/12561063/python-extract-data-from-file/12561163#12561163
            if text.startswith(codecs.BOM_UTF8):
                text = text[3:]
            f.close()
            
            # Create a FeatureExtractor
            extractor = FeatureExtractor(text)
            
            # Save self._spans
            if self._spans:
                assert(self._spans == [list(x) for x in extractor.get_spans(self.atom_type)])
            self._spans = extractor.get_spans(self.atom_type)
            
            # Save self._features
            feature_values = [tup[0] for tup in extractor.get_feature_vectors([feature], self.atom_type)]
            self._features[feature] = feature_values
            
            session.commit()
            return self._features[feature]

class _ReducedDocFeature(Base):
    '''
    This class allows for the features dictionary on ReducedDoc.
    Explained here: http://docs.sqlalchemy.org/en/rel_0_8/orm/extensions/associationproxy.html#proxying-dictionaries
    '''
    __tablename__ = 'reduced_doc_feature'
    reduced_doc_id = Column(Integer, ForeignKey('reduced_doc.id'), primary_key=True)
    feature_id = Column(Integer, ForeignKey('feature.id'), primary_key=True)
    special_key = Column(String)
    reduced_doc = relationship(ReducedDoc, backref=backref(
            "reduced_doc_features",
            collection_class=attribute_mapped_collection("special_key"),
            cascade="all, delete-orphan"
            )
        )
    kw = relationship("_Feature")
    feature = association_proxy('kw', 'feature')
    
class _Feature(Base):
    '''
    This class allows for the features dictionary on ReducedDoc.
    Explained here: http://docs.sqlalchemy.org/en/rel_0_8/orm/extensions/associationproxy.html#proxying-dictionaries
    '''
    __tablename__ = 'feature'
    id = Column(Integer, primary_key=True)
    feature = Column(ARRAY(Float))
    def __init__(self, feature):
        self.feature = feature

class _Figure(Base):
    '''
    This class allows us to store meta data about pdf figures that we create.
    '''
    
    __tablename__ = "figure"
    id = Column(Integer, Sequence("figure_id_seq"), primary_key=True)
    timestamp = Column(DateTime)
    version_number = Column(Integer)
    
    figure_path = Column(String)
    figure_type = Column(String)
    auc = Column(Float)
    
    features = Column(ARRAY(String))
    cluster_type = Column(String)
    k = Column(Integer)
    atom_type = Column(String)
    n = Column(Integer)
    
    def __init__(self, figure_path, figure_type, auc, features, cluster_type, k, atom_type, n):
        
        self.figure_path = figure_path
        self.timestamp = datetime.datetime.now()
        self.version_number = DB_VERSION_NUMBER
        self.figure_type = figure_type
        self.auc = auc
        self.features = features
        self.cluster_type = cluster_type
        self.k = k
        self.atom_type = atom_type
        self.n = n
    
# an Engine, which the Session will use for connection resources
url = "postgresql://%s:%s@%s" % (username, password, dbname)
engine = sqlalchemy.create_engine(url)
# create tables if they don't already exist
Base.metadata.create_all(engine)
# create a configured "Session" class
Session = sessionmaker(bind=engine)

def _test():
    
    session = Session()
    
    first_training_files = IntrinsicUtility().get_n_training_files(3)
    
    rs =  _get_reduced_docs("nchars", first_training_files, session)
    for r in rs:
        print r.get_feature_vectors(['punctuation_percentage',
                                     'stopword_percentage',
                                     'average_sentence_length',
                                     ], session)
    session.close()
    
def _cluster_auc_test(num_plag, num_noplag, mean_diff, std, dimensions = 1, repetitions = 1):
    '''
    roc area under curve evaluation of various clustering techniques
    creates two peaks based on normal distributions and tries to cluster them
    prints out AUC stat for each cluster type
    '''
    print "running cluster auc test with", num_plag, num_noplag, mean_diff, std, dimensions, repetitions
    if repetitions > 1:
        averages = {}

    for rep in range(repetitions):

        noplag_features = []
        for i in range(num_noplag):
            cur = []
            for j in range(dimensions):
                cur.append(scipy.random.normal(0, std))
            noplag_features.append(cur)

        plag_features = []
        for i in range(num_plag):
            cur = []
            for j in range(dimensions):
                cur.append(scipy.random.normal(mean_diff, std))
            plag_features.append(cur)

        features = noplag_features + plag_features
        actuals = [0] * num_noplag + [1] * num_plag

        for clus_type in ["kmeans", "agglom", "hmm"]:
            confidences = cluster(clus_type, 2, features)
            fpr, tpr, thresholds = sklearn.metrics.roc_curve(actuals, confidences, pos_label=1)
            roc_auc = sklearn.metrics.auc(fpr, tpr)
            if repetitions == 1:
                print clus_type, roc_auc
            else:
                averages[clus_type] = averages.get(clus_type, []) + [roc_auc]

    if repetitions > 1:
        for key in averages:
            print key, sum(averages[key])/float(max(1, len(averages[key])))

def _one_run():
    '''
    A general pattern for testing
    '''
    features = FeatureExtractor.get_all_feature_function_names()

    cluster_type = 'kmeans'
    k = 2
    atom_type = 'nchars'
    n = 250
    first_doc_num = 0
    
    print evaluate_n_documents(features, cluster_type, k, atom_type, n, first_doc_num=first_doc_num)

def find_trans_actuals(n, first_doc_num=0):
    first_training_files = IntrinsicUtility().get_n_training_files(n, first_doc_num=first_doc_num)
    features = [
        'punctuation_percentage',
        'gunning_fog_index',
        'syntactic_complexity',
        'num_chars',
        'vowelness_trigram,C,V,C',
        'avg_internal_word_freq_class'
    ]
    session = Session()
    
    reduced_docs = _get_reduced_docs('nchars', first_training_files, session, corpus='intrinsic')
    #reduced_docs = _get_reduced_docs('paragraph', first_training_files, session, corpus='intrinsic')

    actuals = []

    #for d in reduced_docs:
    #    spans = d.get_spans()
    #
    #for i in xrange(len(spans)):
    #    span = spans[i]
    #    actuals.append(1 if d.span_is_plagiarized(span) else 0)

    for doc_index in xrange(len(reduced_docs)):
        doc = reduced_docs[doc_index]
        spans = doc.get_spans()

        for span_index in xrange(len(spans)):
            span = spans[span_index]
            actuals.append(1 if doc.span_is_plagiarized(span) else 0)
            
    return actuals

def find_trans_actuals_main():
    '''
    this is code to be included in the main of testing to find transition probabilities, included for reference

    increment = 0
    transProbs = []

    while (increment<50):

        list_actuals = find_trans_actuals(1,first_doc_num=increment)
        start = list_actuals[0]

        countTrans = 0

        stay0 = 0
        stay1 = 0
        change0 = 0
        change1 = 1

        prev = start

        for i in range(len(list_actuals)):
            #print 'list_actual[i] is ', list_actuals[i]
            cur = list_actuals[i]
            if (i==0):
                pass
            else:
                if (prev == cur):
                    if (cur==0):
                        stay0 +=1
                    else:
                        stay1 +=1
                    prev = cur
                else :
                    countTrans +=1
                    if (prev == 0):
                        change0 +=1
                    else:
                        change1 +=1
                    prev = cur


        if (countTrans == 0):
            transProbs.append([0.01,0.99])
        else:
            transProbs.append([float(change0)/(stay0+change0),float(change1)/(stay1+change1)])

        increment+=1

    print transProbs

    sum1 = [0,0]

    for i in range(len(transProbs)):
        sum1[0] += transProbs[i][0]
        sum1[1] += transProbs[i][1]

    sum1[0] = sum1[0]/len(transProbs)
    sum1[1] = sum1[1]/len(transProbs)

    print sum1
    ''' 

# To see our best runs by AUC (according to the attached JSON files),
# navigate to the figures directory and run:
# ls -t | grep json | xargs grep auc | awk '{print $1, $3; }' | sort -gk 2 | tail -n 20
# Replace the 20 with a larger number to see more results
if __name__ == "__main__":
    # all_features = FeatureExtractor.get_all_feature_function_names(include_nested=True)
    # old_features = FeatureExtractor.get_all_feature_function_names(include_nested=False)
    # nested_features = []
    # for f in all_features:
    #     if f not in old_features:
    #         nested_features.append(f)

    features = [
        'average_sentence_length',
        'average_syllables_per_word',
        'avg_external_word_freq_class',
        'avg_internal_word_freq_class',
        'eissen_feature',
        'flesch_kincaid_grade',
        'flesch_reading_ease',
        'frequency_of_word_been',
        'frequency_of_word_is',
        'frequency_of_word_of',
        'frequency_of_word_the',
        'gunning_fog_index',
        'honore_r_measure',
        'num_chars',
        'punctuation_percentage',
        'stopword_percentage',
        'syntactic_complexity',
        'vowelness_trigram,C,V,V',
        'vowelness_trigram,C,V,C',
        'word_unigram,been',
        'word_unigram,is',
        'word_unigram,of',
        'word_unigram,the',
        'yule_k_characteristic',
    ]

    features = [
        'punctuation_percentage',
        'gunning_fog_index',
        'syntactic_complexity',
        'num_chars',
        'vowelness_trigram,C,V,C',
        'avg_internal_word_freq_class'
    ]
    # features = FeatureExtractor.get_all_feature_function_names()
    # features = [f for f in features if 'evolved' not in f]
    # features = [
    #    'gunning_fog_index',
    #    'syntactic_complexity',
    #    'word_unigram,is',
    #    'average_syllables_per_word'
    # ]
    cluster_type = 'kmeans'
    atom_type = 'nchars'
    n = 20
    print evaluate_n_documents(features, cluster_type, 2, atom_type, n)
    #print evaluate_n_documents(features, 'hmm', 2, atom_type, n)
