# Manage pre-trained embeddings 

from tensorflow.keras.preprocessing import text, sequence
import numpy as np
import sys
import os
import os.path
import json
import lmdb
import io
import zipfile
import gzip
import shutil
import pickle
import hashlib, struct
from tqdm import tqdm
import mmap
import codecs
import tensorflow as tf
#import keras.backend as K

# for fasttext binary embeddings
fasttext_support = True
try:
    import fastText
except ImportError as e:
    fasttext_support = False

from delft.utilities.Tokenizer import tokenizeAndFilterSimple
from delft.utilities.Utilities import download_file

# for ELMo embeddings
from delft.utilities.bilm.data import Batcher, TokenBatcher
from delft.utilities.bilm.model import BidirectionalLanguageModel, dump_token_embeddings
from delft.utilities.bilm.elmo import weight_layers

# gensim is used to exploit .bin FastText embeddings, in particular the OOV with the provided ngrams
#from gensim.models import FastText

# this is the default init size of a lmdb database for embeddings
# based on https://github.com/kermitt2/nerd/blob/master/src/main/java/com/scienceminer/nerd/kb/db/KBDatabase.java
# and https://github.com/kermitt2/nerd/blob/0.0.3/src/main/java/com/scienceminer/nerd/kb/db/KBDatabaseFactory.java#L368
map_size = 100 * 1024 * 1024 * 1024 

# dim of ELMo embeddings (2 times the dim of the LSTM for LM)
ELMo_embed_size = 1024

class Embeddings(object):

    def __init__(self, name, 
        path='delft/resources-registry.json', 
        lang='en', 
        extension='vec', 
        use_ELMo=False, 
        use_cache=True,
        load=True):
        self.name = name
        self.embed_size = 0
        self.static_embed_size = 0
        self.vocab_size = 0
        self.model = {}
        self.registry = self._load_embedding_registry(path)
        self.lang = lang
        self.extension = extension
        self.embedding_lmdb_path = None
        if self.registry is not None:
            self.embedding_lmdb_path = self.registry["embedding-lmdb-path"]
        self.env = None
        if load:
            self.make_embeddings_simple(name)
        self.static_embed_size = self.embed_size
        self.bilm = None

        self.use_cache = use_cache

        # below init for using ELMo embeddings
        self.use_ELMo = use_ELMo
        if use_ELMo:
            self.make_ELMo()
            self.embed_size = ELMo_embed_size + self.embed_size
            description = self.get_description('elmo-'+self.lang)
            self.env_ELMo = None
            if description and description["cache-training"] and self.use_cache:
                self.embedding_ELMo_cache = os.path.join(description["path-cache"], "cache")
                # clean possible remaining cache
                self.clean_ELMo_cache()
                # create and load a cache in write mode, it will be used only for training
                self.env_ELMo = lmdb.open(self.embedding_ELMo_cache, map_size=map_size)

    def __getattr__(self, name):
        return getattr(self.model, name)

    def _load_embedding_registry(self, path='delft/resources-registry.json'):
        """
        Load the description of available embeddings. Each description provides a name, 
        a file path (used only if necessary) and a embeddings type (to take into account
        small variation of format)
        """
        registry_json = open(path).read()
        return json.loads(registry_json)

    def make_embeddings_simple_in_memory(self, name="fasttext-crawl"):
        nbWords = 0
        print('loading embeddings...')
        begin = True
        description = self.get_description(name)
        if description is not None:
            embeddings_path = description["path"]
            self.lang = description["lang"]
            print("path:", embeddings_path)
            if self.extension == 'bin':
                self.model = fastText.load_model(embeddings_path)
                nbWords = len(self.model.get_words())
                self.embed_size = self.model.get_dimension()
            else:
                with open(embeddings_path, encoding='utf8') as f:
                    for line in f:
                        line = line.strip()
                        line = line.split(' ')
                        if begin:
                            begin = False
                            nb_words, embed_size = _fetch_header_if_available(line)

                            # we parse the header
                            if nb_words > 0 and embed_size > 0:
                                nbWords = nb_words
                                self.embed_size = embed_size
                                continue

                        word = line[0]
                        vector = np.array([float(val) for val in line[1:len(line)]], dtype='float32')
                        #else:
                        #    vector = np.array([float(val) for val in line[1:len(line)-1]], dtype='float32')
                        if self.embed_size == 0:
                            self.embed_size = len(vector)
                        self.model[word] = vector
                if nbWords == 0:
                    nbWords = len(self.model)
            print('embeddings loaded for', nbWords, "words and", self.embed_size, "dimensions")

    def make_embeddings_lmdb(self, name="fasttext-crawl"):
        print('\nCompiling embeddings... (this is done only one time per embeddings at first usage)')
        description = self.get_description(name)

        if description is None:
            print('\nNo description found in embeddings registry for embeddings', name)
            return

        if description is not None:
            # the following method will possibly download the mebedding file if not available locally
            embeddings_path = self.get_embedding_path(description)
            if embeddings_path is None:
                print('\nCould not locate a usable resource for embeddings', name)
                return

            self.load_embeddings_from_file(embeddings_path)

        # cleaning possible downloaded embeddings
        self.clean_downloads()

    def load_embeddings_from_file(self, embeddings_path):
        begin = True
        nbWords = 0
        txn = self.env.begin(write=True)
        # batch_size = 1024
        i = 0
        nb_lines = 0

        # read number of lines first
        embedding_file = open_embedding_file(embeddings_path)
        if embedding_file is None:
            print("Error: could not open embeddings file", embeddings_path)
            return

        for line in embedding_file:
            nb_lines += 1
        embedding_file.close()

        embedding_file = open_embedding_file(embeddings_path)
        #with open(embeddings_path, encoding='utf8') as f:
        for line in tqdm(embedding_file, total=nb_lines):
            line = line.decode()
            line = line.split(' ')
            if begin:
                begin = False
                nb_words, embed_size = _fetch_header_if_available(line)

                if nb_words > 0 and embed_size > 0:
                    nbWords = nb_words
                    self.embed_size = embed_size
                    continue

            word = line[0]
            try:
                if line[len(line)-1] == '\n':
                    vector = np.array([float(val) for val in line[1:len(line)-1]], dtype='float32')
                else:
                    vector = np.array([float(val) for val in line[1:len(line)]], dtype='float32')
            
                #vector = np.array([float(val) for val in line[1:len(line)]], dtype='float32')
            except:
                print(len(line))
                print(line[1:len(line)])
            #else:
            #    vector = np.array([float(val) for val in line[1:len(line)-1]], dtype='float32')
            if self.embed_size == 0:
                self.embed_size = len(vector)

            if len(word.encode(encoding='UTF-8')) < self.env.max_key_size():   
                txn.put(word.encode(encoding='UTF-8'), _serialize_pickle(vector))  
                #txn.put(word.encode(encoding='UTF-8'), _serialize_byteio(vector))
                i += 1

            # commit batch
            # if i % batch_size == 0:
            #     txn.commit()
            #     txn = self.env.begin(write=True)

        embedding_file.close()

        #if i % batch_size != 0:
        txn.commit()   
        if nbWords == 0:
            nbWords = i
        self.vocab_size = nbWords
        print('embeddings loaded for', nbWords, "words and", self.embed_size, "dimensions")

    def clean_downloads(self):
        # cleaning possible downloaded embeddings
        if 'embedding-download-path' in self.registry and os.path.exists(self.registry['embedding-download-path']) and os.path.isdir(self.registry['embedding-download-path']):
            for filename in os.listdir(self.registry['embedding-download-path']):
                file_path = os.path.join(self.registry['embedding-download-path'], filename)
                try:
                    if os.path.isfile(file_path) or os.path.islink(file_path):
                        os.unlink(file_path)
                    elif os.path.isdir(file_path):
                        shutil.rmtree(file_path)
                except Exception as e:
                    print('Failed to delete %s. Reason: %s' % (file_path, e))

    def make_embeddings_simple(self, name="fasttext-crawl"):
        description = self.get_description(name)
        if description is not None:
            self.extension = description["format"]

        if self.extension == "bin":
            if fasttext_support == True:
                print("embeddings are of .bin format, so they will be loaded in memory...")
                self.make_embeddings_simple_in_memory(name)
            else:
                if not (sys.platform == 'linux' or sys.platform == 'darwin'):
                    raise ValueError('FastText .bin format not supported for your platform')
                else:
                    raise ValueError('Go to the documentation to get more information on how to install FastText .bin support')

        elif self.embedding_lmdb_path is None or self.embedding_lmdb_path == "None":
            print("embedding_lmdb_path is not specified in the embeddings registry, so the embeddings will be loaded in memory...")
            self.make_embeddings_simple_in_memory(name)
        else:    
            # if the path to the lmdb database files does not exist, we create it
            if not os.path.isdir(self.embedding_lmdb_path):
                # conservative check (likely very useless)
                if not os.path.exists(self.embedding_lmdb_path):
                    os.makedirs(self.embedding_lmdb_path)

            # check if the lmdb database exists
            envFilePath = os.path.join(self.embedding_lmdb_path, name)
            load_db = True
            if os.path.isdir(envFilePath):
                description = self.get_description(name)
                if description is not None:
                    self.lang = description["lang"]

                # open the database in read mode
                self.env = lmdb.open(envFilePath, readonly=True, max_readers=2048, max_spare_txns=4)
                if self.env:
                    # we need to set self.embed_size and self.vocab_size
                    with self.env.begin() as txn:
                        stats = txn.stat()
                        size = stats['entries']
                        self.vocab_size = size

                    with self.env.begin() as txn:
                        cursor = txn.cursor()
                        for key, value in cursor:
                            vector = _deserialize_pickle(value)
                            self.embed_size = vector.shape[0]
                            break
                        cursor.close()

                    if self.vocab_size > 100 and self.embed_size > 10:
                        # lmdb database exists and looks valid
                        load_db = False

                        # no idea why, but we need to close and reopen the environment to avoid
                        # mdb_txn_begin: MDB_BAD_RSLOT: Invalid reuse of reader locktable slot
                        # when opening new transaction !
                        self.env.close()
                        self.env = lmdb.open(envFilePath, readonly=True, max_readers=2048, max_spare_txns=2)

            if load_db: 
                # create and load the database in write mode
                self.env = lmdb.open(envFilePath, map_size=map_size)
                self.make_embeddings_lmdb(name)

    #@tf.function
    def make_ELMo(self):
        # Location of pretrained BiLM for the specified language
        # TBD check if ELMo language resources are present
        description = self.get_description('elmo-'+self.lang)
        if description is not None:
            self.lang = description["lang"]
            vocab_file = description["path-vocab"]
            options_file = description["path-config"]
            weight_file = description["path_weights"]

            print('init ELMo')

            # Create a Batcher to map text to character ids
            self.batcher = Batcher(vocab_file, 50)

            # Build the biLM graph.
            self.bilm = BidirectionalLanguageModel(self.lang, options_file, weight_file)

            # Input placeholders to the biLM.
            self.character_ids = tf.compat.v1.placeholder('int32', shape=(None, None, 50))

            with tf.compat.v1.variable_scope(self.lang, reuse=tf.compat.v1.AUTO_REUSE):
                # the reuse=True scope reuses weights from the whole context 
                self.embeddings_op = self.bilm(self.character_ids)
                self.elmo_input = weight_layers('input', self.embeddings_op, l2_coef=0.0)

    def get_description(self, name):
        for emb in self.registry["embeddings"]:
            if emb["name"] == name:
                return emb
        for emb in self.registry["embeddings-contextualized"]:
            if emb["name"] == name:
                return emb
        for emb in self.registry["transformers"]:
            if emb["name"] == name:
                return emb
        return None

    def get_word_vector(self, word):
        """
            Get static embeddings (e.g. glove) for a given token
        """
        if (self.name == 'wiki.fr') or (self.name == 'wiki.fr.bin'):
            # the pre-trained embeddings are not cased
            word = word.lower()
        if self.env is None or self.extension == 'bin':
            # db not available or embeddings in bin format, the embeddings should be available in memory (normally!)
            return self.get_word_vector_in_memory(word)
        try:    
            with self.env.begin() as txn:
                vector = txn.get(word.encode(encoding='UTF-8'))
                if vector:
                    word_vector = _deserialize_pickle(vector)
                    vector = None
                else:
                    word_vector = np.zeros((self.static_embed_size,), dtype=np.float32)
                    # alternatively, initialize with random negative values
                    #word_vector = np.random.uniform(low=-0.5, high=0.0, size=(self.embed_size,))
                    # alternatively use fasttext OOV ngram possibilities (if ngram available)
        except lmdb.Error:
            # no idea why, but we need to close and reopen the environment to avoid
            # mdb_txn_begin: MDB_BAD_RSLOT: Invalid reuse of reader locktable slot
            # when opening new transaction !
            self.env.close()
            envFilePath = os.path.join(self.embedding_lmdb_path, self.name)
            self.env = lmdb.open(envFilePath, readonly=True, max_readers=2048, max_spare_txns=2, lock=False)
            return self.get_word_vector(word)
        return word_vector

    def get_word_vector_in_memory(self, word):
        if (self.name == 'wiki.fr') or (self.name == 'wiki.fr.bin'):
            # the pre-trained embeddings are not cased
            word = word.lower()
        if self.extension == 'bin':
            return self.model.get_word_vector(word)
        if word in self.model:
            return self.model[word]
        else:
            # for unknown word, we use a vector filled with 0.0
            return np.zeros((self.static_embed_size,), dtype=np.float32)
            # alternatively, initialize with random negative values
            #return np.random.uniform(low=-0.5, high=0.0, size=(self.embed_size,))
            # alternatively use fasttext OOV ngram possibilities (if ngram available)

    def get_embedding_path(self, description):
        embeddings_path = None
        if "path" in description:
            embeddings_path = description["path"]    
        self.lang = description["lang"]
        
        if embeddings_path is None or not os.path.isfile(embeddings_path):
            print("error: embedding path for", description['name'], "is not valid", embeddings_path)
            if "url" in description and len(description["url"])>0:
                url = description["url"]
                download_path = self.registry['embedding-download-path']
                # if the download path does not exist, we create it
                if not os.path.isdir(download_path):
                    try:
                        os.mkdir(download_path)
                    except OSError:
                        print ("Creation of the download directory", download_path, "failed")

                print("Downloading resource file for", description['name'], "...")
                embeddings_path = download_file(url, download_path)
                if embeddings_path != None and os.path.isfile(embeddings_path):
                    print("Download sucessful:", embeddings_path)
            else:
                print("no download url available for this embeddings resource, please review the embedding registry for", description['name'])
        return embeddings_path


    def get_sentence_vector_only_ELMo(self, token_list):
        """
            Return the ELMo embeddings only for a full sentence
        """

        if not self.use_ELMo:
            print("Warning: ELMo embeddings requested but embeddings object wrongly initialised")
            return

        # Create batches of data
        local_token_ids = self.batcher.batch_sentences(token_list)
        max_size_sentence = local_token_ids[0].shape[0]
        # check lmdb cache
        elmo_result = self.get_ELMo_lmdb_vector(token_list, max_size_sentence)
        if elmo_result is not None:
            return elmo_result

        with tf.Session() as sess:
            # weird, for this cpu is faster than gpu (1080Ti !)
            with tf.device("/cpu:0"):
                # It is necessary to initialize variables once before running inference
                sess.run(tf.global_variables_initializer())

                # Compute ELMo representations (2 times as a heavy warm-up)
                elmo_result = sess.run(
                    self.elmo_input['weighted_op'],
                    feed_dict={self.character_ids: local_token_ids}
                )
                elmo_result = sess.run(
                    self.elmo_input['weighted_op'],
                    feed_dict={self.character_ids: local_token_ids}
                )
                #cache computation
                self.cache_ELMo_lmdb_vector(token_list, elmo_result)
        return elmo_result

    def get_sentence_vector_with_ELMo(self, token_list):
        """
            Return a concatenation of standard embeddings (e.g. Glove) and ELMo embeddings 
            for a full sentence
        """
        if not self.use_ELMo:
            print("Warning: ELMo embeddings requested but embeddings object wrongly initialised")
            return

        #print("\ntoken_list:", token_list)
        local_token_ids = self.batcher.batch_sentences(token_list)
        #print("local_token_ids:", local_token_ids)
        max_size_sentence = local_token_ids[0].shape[0]

        elmo_result = self.get_ELMo_lmdb_vector(token_list, max_size_sentence) 
        if elmo_result is None:
            with tf.compat.v1.Session() as sess:
                # weird, for this cpu is faster than gpu (1080Ti !)
                with tf.device("/cpu:0"):
                    # It is necessary to initialize variables once before running inference
                    sess.run(tf.compat.v1.global_variables_initializer())

                    # Compute ELMo representations (2 times as a heavy warm-up)
                    elmo_result = sess.run(
                        self.elmo_input['weighted_op'],
                        feed_dict={self.character_ids: local_token_ids}
                    )
                    elmo_result = sess.run(
                        self.elmo_input['weighted_op'],
                        feed_dict={self.character_ids: local_token_ids}
                    )
                    #cache computation
                    self.cache_ELMo_lmdb_vector(token_list, elmo_result)
        
        concatenated_result = np.zeros((len(token_list), max_size_sentence-2, self.embed_size), dtype=np.float32)
        #concatenated_result = np.random.rand(elmo_result.shape[0], max_size_sentence-2, self.embed_size)
        for i in range(0, len(token_list)):
            for j in range(0, len(token_list[i])):
                #if is_int(token_list[i][j]) or is_float(token_list[i][j]):
                #dummy_result = np.zeros((elmo_result.shape[2]), dtype=np.float32)
                #concatenated_result[i][j] = np.concatenate((dummy_result, self.get_word_vector(token_list[i][j])), )
                #else:
                concatenated_result[i][j] = np.concatenate((elmo_result[i][j], self.get_word_vector(token_list[i][j]).astype('float32')), )
                #concatenated_result[i][j] = np.concatenate((self.get_word_vector(token_list[i][j]), elmo_result[i][j]), )
        return concatenated_result

    def get_ELMo_lmdb_vector(self, token_list, max_size_sentence):
        """
            Try to get the ELMo embeddings for a sequence cached in LMDB
        """
        if self.env_ELMo is None:
            # db cache not available, we don't cache ELMo stuff
            return None
        try:    
            ELMo_vector = np.zeros((len(token_list), max_size_sentence-2, ELMo_embed_size), dtype='float32')
            with self.env_ELMo.begin() as txn:
                for i in range(0, len(token_list)):
                    txn = self.env_ELMo.begin()
                    # get a hash for the token_list
                    the_hash = list_digest(token_list[i])
                    vector = txn.get(the_hash.encode(encoding='UTF-8'))
                    if vector:
                        # adapt expected shape/padding
                        local_embeddings = _deserialize_pickle(vector)
                        if local_embeddings.shape[0] > max_size_sentence-2:
                            # squeeze the extra padding space
                            ELMo_vector[i] = local_embeddings[:max_size_sentence-2,]
                        elif local_embeddings.shape[0] == max_size_sentence-2:
                            # bingo~!
                            ELMo_vector[i] = local_embeddings
                        else:
                            # fill the missing space with padding
                            filler = np.zeros((max_size_sentence-(local_embeddings.shape[0]+2), ELMo_embed_size), dtype='float32')
                            ELMo_vector[i] = np.concatenate((local_embeddings, filler))
                        vector = None
                    else:
                        return None
        except lmdb.Error:
            # no idea why, but we need to close and reopen the environment to avoid
            # mdb_txn_begin: MDB_BAD_RSLOT: Invalid reuse of reader locktable slot
            # when opening new transaction !
            self.env_ELMo.close()
            self.env_ELMo = lmdb.open(self.embedding_ELMo_cache, readonly=True, max_readers=2048, max_spare_txns=2, lock=False)
            return self.get_ELMo_lmdb_vector(token_list)
        return ELMo_vector

    def cache_ELMo_lmdb_vector(self, token_list, ELMo_vector):
        """
            Cache in LMDB the ELMo embeddings for a given sequence 
        """
        if self.env_ELMo is None:
            # db cache not available, we don't cache ELMo stuff
            return None
        txn = self.env_ELMo.begin(write=True)
        for i in range(0, len(token_list)):
            # get a hash for the token_list
            the_hash = list_digest(token_list[i])
            txn.put(the_hash.encode(encoding='UTF-8'), _serialize_pickle(ELMo_vector[i]))  
        txn.commit()

    def clean_ELMo_cache(self):
        """
            Delete ELMo embeddings cache, this takes place normally after the completion of a training
        """
        if self.env_ELMo is None:
            # db cache not available, nothing to clean
            return
        else: 
            self.env_ELMo.close()
            self.env_ELMo = None
            for file in os.listdir(self.embedding_ELMo_cache): 
                file_path = os.path.join(self.embedding_ELMo_cache, file)
                if os.path.isfile(file_path):
                    os.remove(file_path)
            os.rmdir(self.embedding_ELMo_cache)

def _serialize_byteio(array):
    memfile = io.BytesIO()
    np.save(memfile, array)
    memfile.seek(0)
    return memfile.getvalue()


def _deserialize_byteio(serialized):
    memfile = io.BytesIO()
    memfile.write(serialized)
    memfile.seek(0)
    return np.load(memfile)


def _serialize_pickle(a):
    return pickle.dumps(a)


def _deserialize_pickle(serialized):
    return pickle.loads(serialized)

def open_embedding_file(embeddings_path):
    # embeddings can be uncompressed or compressed with gzip or zip
    if embeddings_path.endswith(".gz"):
        embedding_file = gzip.open(embeddings_path, mode="rb")
    elif embeddings_path.endswith(".zip"):
        zip_file = zipfile.ZipFile(embeddings_path, 'r')
        for filename in zip_file.namelist():
            print(filename)
            if filename.endswith("vec") or filename.endswith("txt"):
                embedding_file = zip_file.open(filename, mode="r")
    else:
        embedding_file = open(embeddings_path, mode="rb")
    return embedding_file

def _get_num_lines(file_path):
    fp = open(file_path, "r+")
    buf = mmap.mmap(fp.fileno(), 0)
    lines = 0
    while buf.readline():
        lines += 1
    fp.close()
    return lines

def list_digest(strings):
    hash = hashlib.sha1()
    for s in strings:
        hash.update(struct.pack("I", len(s)))
        hash.update(s.encode(encoding='UTF-8'))
    return hash.hexdigest()

def is_int(s):
    try: 
        int(s)
        return True
    except ValueError:
        return False

def is_float(s):
    try:
        float(s)
        return True
    except ValueError:
        return False

def _fetch_header_if_available(line):
    """
    Fetch the header if the line is just composed by two elements the number of workds and the embedding size

    :param line: a splitted line (if not split, tried to split by spaces)
    :return: the number of words and the embedding size, if there is no header, they will be set to -1
    """
    if type(line) == 'str':
        line = line.split(" ")

    nb_words = -1
    embed_size = -1

    if len(line) == 2:
        # first line gives the nb of words and the embedding size
        nb_words = int(line[0])
        embed_size = int(line[1].replace("\n", ""))

    return nb_words, embed_size
