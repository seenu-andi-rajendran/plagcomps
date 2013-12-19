class Passage:
	'''
	A class to store/query information about a passage of text, including
	where it came from (doc_name), the type of atom used (char, word, sentence, 
	or paragraph, what chunk it refers to (text from start -> end), and its 
	stylometric features (a dictionary mapping feature => value)

	Once clustering is performed, sets the passage's cluster number
	as well
	'''
	
	def __init__(self, doc_name, atom_type, start_word_index, end_word_index, text, features = {}):
		self.doc_name = doc_name
		self.atom_type = atom_type
		self.start_word_index = start_word_index
		self.end_char_index = end_word_index
		self.text = text
		self.features = features
		self.cluster_num = None

	def __str__(self):
		return 'Text: ' + self.text + \
		'\nFeatures: ' + str(self.features) +  \
		'\nCluster num: ' + str(self.cluster_num) + \
		'\n------------'

	def to_json(self):
		'''
		Returns a dictionary representation of the passage
		'''
		json_rep = {
			'atom_type' : self.atom_type,
			'start_word_index' : self.start_word_index,
			'end_word_index' : self.end_char_index,
			'text' : self.text,
			'features' : self.features
		}
		
		return json_rep

	def to_html(self):
		html = ''
		for feat, value in self.features.iteritems():
			html += '<p>%s</p><p>%.4f</p>' % (feat, value)
			html += '<hr size="10">'

		return html

	def assign_cluster(self, cluster_num):
		'''
		Sets self.cluster_num to cluster_num. Returns None.
		'''
		self.cluster_num = cluster_num


