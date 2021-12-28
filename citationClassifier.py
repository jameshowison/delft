import json
from delft.utilities.Embeddings import Embeddings
from delft.utilities.Utilities import split_data_and_labels
from delft.textClassification.reader import load_citation_sentiment_corpus
import delft.textClassification
from delft.textClassification import Classifier
import argparse
import time
from delft.textClassification.models import modelTypes

list_classes = ["negative", "neutral", "positive"]
class_weights = {0: 25.,
                 1: 1.,
                 2: 9.}

def train(embeddings_name, fold_count, architecture="gru"):
    batch_size, maxlen, bert_type, architecture = configure(architecture)

    model = Classifier('citations', model_type=architecture, list_classes=list_classes, max_epoch=100, fold_number=fold_count, patience=10,
        use_roc_auc=True, embeddings_name=embeddings_name, batch_size=batch_size, maxlen=maxlen,
        class_weights=class_weights, bert_type=bert_type)

    print('loading citation sentiment corpus...')
    xtr, y = load_citation_sentiment_corpus("data/textClassification/citations/citation_sentiment_corpus.txt")

    if fold_count == 1:
        model.train(xtr, y)
    else:
        model.train_nfold(xtr, y)
    # saving the model
    model.save()


def configure(architecture):
    batch_size = 256
    maxlen = 150
    bert_type = None

    # default bert model parameters
    if architecture == "scibert":
        batch_size = 32
        bert_type = "scibert-cased"
        architecture = "bert"
    elif architecture.find("bert") != -1:
        batch_size = 32
        bert_type = "bert-base-en-cased"
        architecture = "bert"

    return batch_size, maxlen, bert_type, architecture


def train_and_eval(embeddings_name, fold_count, architecture="gru"): 
    batch_size, maxlen, bert_type, architecture = configure(architecture)

    model = Classifier('citations', model_type=architecture, list_classes=list_classes, max_epoch=100, fold_number=fold_count, patience=10,
        use_roc_auc=True, embeddings_name=embeddings_name, batch_size=batch_size, maxlen=maxlen,
        class_weights=class_weights, bert_type=bert_type)

    print('loading citation sentiment corpus...')
    xtr, y = load_citation_sentiment_corpus("data/textClassification/citations/citation_sentiment_corpus.txt")

    # segment train and eval sets
    x_train, y_train, x_test, y_test = split_data_and_labels(xtr, y, 0.9)

    if fold_count == 1:
        model.train(x_train, y_train)
    else:
        model.train_nfold(x_train, y_train)
    model.eval(x_test, y_test)

    # saving the model
    model.save()


# classify a list of texts
def classify(texts, output_format, architecture="gru"):
    # load model
    model = Classifier('citations', model_type=architecture, list_classes=list_classes)
    model.load()
    start_time = time.time()
    result = model.predict(texts, output_format)
    runtime = round(time.time() - start_time, 3)
    if output_format == 'json':
        result["runtime"] = runtime
    else:
        print("runtime: %s seconds " % (runtime))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description = "Sentiment classification of citation passages")

    parser.add_argument("action")
    parser.add_argument("--fold-count", type=int, default=1)
    parser.add_argument("--architecture",default='gru', help="type of model architecture to be used, one of "+str(modelTypes))
    parser.add_argument(
        "--embedding", default='word2vec',
        help=(
            "The desired pre-trained word embeddings using their descriptions in the file"
            " embedding-registry.json."
            " Be sure to use here the same name as in the registry ('glove-840B', 'fasttext-crawl', 'word2vec'),"
            " and that the path in the registry to the embedding file is correct on your system."
        )
    )

    args = parser.parse_args()

    if args.action not in ('train', 'train_eval', 'classify'):
        print('action not specifed, must be one of [train,train_eval,classify]')

    embeddings_name = args.embedding
    architecture = args.architecture
    if architecture not in modelTypes:
        print('unknown model architecture, must be one of '+str(modelTypes))

    if args.action == 'train':
        if args.fold_count < 1:
            raise ValueError("fold-count should be equal or more than 1")

        train(embeddings_name, args.fold_count, architecture=architecture)

    if args.action == 'train_eval':
        if args.fold_count < 1:
            raise ValueError("fold-count should be equal or more than 1")

        y_test = train_and_eval(embeddings_name, args.fold_count, architecture=architecture)    

    if args.action == 'classify':
        someTexts = ['One successful strategy [15] computes the set-similarity involving (multi-word) keyphrases about the mentions and the entities, collected from the KG.', 
            'Unfortunately, fewer than half of the OCs in the DAML02 OC catalog (Dias et al. 2002) are suitable for use with the isochrone-fitting method because of the lack of a prominent main sequence, in addition to an absence of radial velocity and proper-motion data.', 
            'However, we found that the pairwise approach LambdaMART [41] achieved the best performance on our datasets among most learning to rank algorithms.']
        result = classify(someTexts, "json", architecture=architecture)
        print(json.dumps(result, sort_keys=False, indent=4, ensure_ascii=False))
