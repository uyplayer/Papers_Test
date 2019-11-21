# -*- coding: utf-8 -*-
import numpy as np
import torch
from torch.nn import init
from torch.nn.parameter import Parameter


class SelfAttention(torch.nn.Module):
    def __init__(self, wv_dim, maxlen):
        super(SelfAttention, self).__init__()
        self.wv_dim = wv_dim # 维度

        # max sentence length -- batch 2nd dim size
        self.maxlen = maxlen # 句子长度
        self.M = Parameter(torch.Tensor(wv_dim, wv_dim)) # Parameters 是 Variable 的子类 就是网络的参数
        init.kaiming_uniform(self.M.data) # 求均值

        # softmax for attending to wod vectors
        self.attention_softmax = torch.nn.Softmax() #  softmax 函数

    def forward(self, input_embeddings):
        # (b, wv, 1)
        mean_embedding = torch.mean(input_embeddings, (1,)).unsqueeze(2) #求均值（行），并添加一列

        # (wv, wv) x (b, wv, 1) -> (b, wv, 1)
        product_1 = torch.matmul(self.M, mean_embedding) # 网络的参数*均值

        # (b, maxlen, wv) x (b, wv, 1) -> (b, maxlen, 1)
        product_2 = torch.matmul(input_embeddings, product_1).squeeze(2)

        results = self.attention_softmax(product_2)  # 通过softmax处理

        return results

    def extra_repr(self):
        return 'wv_dim={}, maxlen={}'.format(self.wv_dim, self.maxlen)


# 模型
class ABAE(torch.nn.Module):
    """
        The model described in the paper ``An Unsupervised Neural Attention Model for Aspect Extraction''
        by He, Ruidan and  Lee, Wee Sun  and  Ng, Hwee Tou  and  Dahlmeier, Daniel, ACL2017
        https://aclweb.org/anthology/papers/P/P17/P17-1036/

    """

    def __init__(self, wv_dim=200, asp_count=30, ortho_reg=0.1, maxlen=201, init_aspects_matrix=None):
        """
        Initializing the model

        :param wv_dim: word vector size
        :param asp_count: number of aspects
        :param ortho_reg: coefficient for tuning the ortho-regularizer's influence
        :param maxlen: sentence max length taken into account
        :param init_aspects_matrix: None or init. matrix for aspects
        """
        super(ABAE, self).__init__()
        self.wv_dim = wv_dim
        self.asp_count = asp_count
        self.ortho = ortho_reg
        self.maxlen = maxlen

        self.attention = SelfAttention(wv_dim, maxlen)
        self.linear_transform = torch.nn.Linear(self.wv_dim, self.asp_count)
        self.softmax_aspects = torch.nn.Softmax()
        self.aspects_embeddings = Parameter(torch.Tensor(wv_dim, asp_count)) # 网络变量

        if init_aspects_matrix is None:
            torch.nn.init.xavier_uniform(self.aspects_embeddings)
        else:
            self.aspects_embeddings.data = torch.from_numpy(init_aspects_matrix.T)

    def get_aspects_importances(self, text_embeddings):
        """
            Takes embeddings of a sentence as input, returns attention weights
        """

        # compute attention scores, looking at text embeddings average
        attention_weights = self.attention(text_embeddings)

        # multiplying text embeddings by attention scores -- and summing
        # (matmul: we sum every word embedding's coordinate with attention weights)
        weighted_text_emb = torch.matmul(attention_weights.unsqueeze(1), # (batch, 1, sentence)
                                         text_embeddings                 # (batch, sentence, wv_dim)
                                         ).squeeze()

        # encoding with a simple feed-forward layer (wv_dim) -> (aspects_count)
        raw_importances = self.linear_transform(weighted_text_emb)

        # computing 'aspects distribution in a sentence'
        aspects_importances = self.softmax_aspects(raw_importances)

        return attention_weights, aspects_importances, weighted_text_emb

    def forward(self, text_embeddings, negative_samples_texts):

        # negative samples are averaged
        averaged_negative_samples = torch.mean(negative_samples_texts, dim=2)

        # encoding: words embeddings -> sentence embedding, aspects importances
        _, aspects_importances, weighted_text_emb = self.get_aspects_importances(text_embeddings)

        # decoding: aspects embeddings matrix, aspects_importances -> recovered sentence embedding
        recovered_emb = torch.matmul(self.aspects_embeddings, aspects_importances.unsqueeze(2)).squeeze()

        # loss
        reconstruction_triplet_loss = ABAE._reconstruction_loss(weighted_text_emb,
                                                                recovered_emb,
                                                                averaged_negative_samples)
        max_margin = torch.max(reconstruction_triplet_loss, torch.zeros_like(reconstruction_triplet_loss))

        return self.ortho * self._ortho_regularizer() + max_margin

    @staticmethod
    def _reconstruction_loss(text_emb, recovered_emb, averaged_negative_emb):

        positive_dot_products = torch.matmul(text_emb.unsqueeze(1), recovered_emb.unsqueeze(2)).squeeze()
        negative_dot_products = torch.matmul(averaged_negative_emb, recovered_emb.unsqueeze(2)).squeeze()
        reconstruction_triplet_loss = torch.sum(1 - positive_dot_products.unsqueeze(1) + negative_dot_products, dim=1)

        return reconstruction_triplet_loss

    def _ortho_regularizer(self):
        return torch.norm(
            torch.matmul(self.aspects_embeddings.t(), self.aspects_embeddings) \
            - torch.eye(self.asp_count))

    def get_aspect_words(self, w2v_model, topn=15):
        words = []

        # getting aspects embeddings
        aspects = self.aspects_embeddings.detach().numpy()

        # getting scalar products of word embeddings and aspect embeddings;
        # to obtain the ``probabilities'', one should also apply softmax
        words_scores = w2v_model.wv.syn0.dot(aspects)

        for row in range(aspects.shape[1]):
            argmax_scalar_products = np.argsort(- words_scores[:, row])[:topn]
            # print([w2v_model.wv.index2word[i] for i in argmax_scalar_products])
            # print([w for w, dist in w2v_model.similar_by_vector(aspects.T[row])[:topn]])
            words.append([w2v_model.wv.index2word[i] for i in argmax_scalar_products])

        return words
