"""
This file is for models creation, which consults options
and creates each encoder and decoder accordingly.
"""

from abstractive.optimizer import Optimizer
from abstractive.transformer_encoder import TransformerEncoder, TransformerInterEncoder
from abstractive.transformer_decoder import TransformerDecoder


"""
Implementation of "Convolutional Sequence to Sequence Learning"
"""
import torch.nn as nn
from torch.nn.init import xavier_uniform_
import torch

def build_optim(args, model, checkpoint):
    """ Build optimizer """
    saved_optimizer_state_dict = None

    if args.train_from != '':
        optim = checkpoint['optim']
        saved_optimizer_state_dict = optim.optimizer.state_dict()
    else:
        optim = Optimizer(
            args.optim, args.lr, args.max_grad_norm,
            beta1=args.beta1, beta2=args.beta2,
            decay_method=args.decay_method,
            warmup_steps=args.warmup_steps, model_size=args.enc_hidden_size)

    # Stage 1:
    # Essentially optim.set_parameters (re-)creates and optimizer using
    # model.paramters() as parameters that will be stored in the
    # optim.optimizer.param_groups field of the torch optimizer class.
    # Importantly, this method does not yet load the optimizer state, as
    # essentially it builds a new optimizer with empty optimizer state and
    # parameters from the model.
    optim.set_parameters(list(model.named_parameters()))

    if args.train_from != '':

        # optimizer from a checkpoint, we load the saved_optimizer_state_dict
        # into the re-created optimizer, to set the optim.optimizer.state
        # field, which was previously empty. For this, we use the optimizer
        # state saved in the "saved_optimizer_state_dict" variable for
        # this purpose.
        # See also: https://github.com/pytorch/pytorch/issues/2830
        optim.optimizer.load_state_dict(saved_optimizer_state_dict)
        # Convert back the state values to cuda type if applicable
        if args.visible_gpus != '-1':
            for state in optim.optimizer.state.values():
                for k, v in state.items():
                    if torch.is_tensor(v):
                        state[k] = v.cuda()

        # We want to make sure that indeed we have a non-empty optimizer state
        # when we loaded an existing model. This should be at least the case
        # for Adam, which saves "exp_avg" and "exp_avg_sq" state
        # (Exponential moving average of gradient and squared gradient values)
        if (optim.method == 'adam') and (len(optim.optimizer.state) < 1):
            raise RuntimeError(
                "Error: loaded Adam optimizer from existing model" +
                " but optimizer state is empty")

    return optim


def get_generator(dec_hidden_size, vocab_size, device):
    gen_func = nn.LogSoftmax(dim=-1)
    generator = nn.Sequential(
        nn.Linear(dec_hidden_size, vocab_size),
        gen_func
    )
    generator.to(device)

    return generator


class Summarizer(nn.Module):
    def __init__(self,args, word_padding_idx, vocab_size, device, checkpoint=None):
        self.args = args
        super(Summarizer, self).__init__()
        # self.spm = spm
        self.vocab_size = vocab_size
        self.device = device
        # src_dict = fields["src"].vocab
        # tgt_dict = fields["tgt"].vocab

        src_embeddings = torch.nn.Embedding(self.vocab_size, self.args.emb_size, padding_idx=word_padding_idx)
        tgt_embeddings = torch.nn.Embedding(self.vocab_size, self.args.emb_size, padding_idx=word_padding_idx)

        if (self.args.share_embeddings):
            tgt_embeddings.weight = src_embeddings.weight

        if (self.args.hier):
            if (self.args.inter_att == 2):
                self.encoder = TransformerInterEncoder(self.args.enc_layers, self.args.enc_hidden_size, self.args.heads,
                                                       self.args.ff_size, self.args.enc_dropout, src_embeddings,
                                                       inter_att_version=2, inter_layers=self.args.inter_layers, inter_heads= self.args.inter_heads, device=device)
            elif (self.args.inter_att == 3):
                self.encoder = TransformerInterEncoder(self.args.enc_layers, self.args.enc_hidden_size, self.args.heads,
                                                       self.args.ff_size, self.args.enc_dropout, src_embeddings,
                                                       inter_att_version=3, inter_layers=self.args.inter_layers, inter_heads=self.args.inter_heads, device=device)
        else:
            self.encoder = TransformerEncoder(self.args.enc_layers, self.args.enc_hidden_size, self.args.heads,
                                              self.args.ff_size,
                                              self.args.enc_dropout, src_embeddings)

        self.decoder = TransformerDecoder(
            self.args.dec_layers,
            self.args.dec_hidden_size, heads=self.args.heads,
            d_ff=self.args.ff_size, dropout=self.args.dec_dropout, embeddings=tgt_embeddings)

        self.generator = get_generator(self.args.dec_hidden_size, self.vocab_size, device)
        if self.args.share_decoder_embeddings:
            self.generator[0].weight = self.decoder.embeddings.weight

        if checkpoint is not None:
            # checkpoint['model']
            keys = list(checkpoint['model'].keys())
            for k in keys:
                if ('a_2' in k):
                    checkpoint['model'][k.replace('a_2', 'weight')] = checkpoint['model'][k]
                    del (checkpoint['model'][k])
                if ('b_2' in k):
                    checkpoint['model'][k.replace('b_2', 'bias')] = checkpoint['model'][k]
                    del (checkpoint['model'][k])

            self.load_state_dict(checkpoint['model'], strict=True)
        else:
            for p in self.parameters():
                if p.dim() > 1:
                    xavier_uniform_(p)



        self.to(device)

    def forward(self, src, tgt):
        tgt = tgt[:-1]
        # print(src.size())
        # print(tgt.size())

        src_features, mask_hier = self.encoder(src)
        dec_state = self.decoder.init_decoder_state(src, src_features)
        # dec_state = self.decoder.init_decoder_state(src, src_features)
        if (self.args.hier):
            decoder_outputs = self.decoder(tgt, src_features, dec_state, memory_masks=mask_hier)
        else:
            decoder_outputs = self.decoder(tgt, src_features, dec_state)


        return decoder_outputs


