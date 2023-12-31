from typing import List
import torch 
import torch.nn as nn 
from transformers import AutoModel, AutoTokenizer
from ..componets import AttentionWithContext, ExtraRoberta


### Bi-encoder 
class BiEncoder(nn.Module):
    def __init__(self, model_name= 'vinai/phobert-base-v2', required_grad= True, 
                 dropout= 0.1, hidden_dim= 768, num_label= None):
        super(BiEncoder, self).__init__()

        self.model= AutoModel.from_pretrained(model_name, output_hidden_states= True)

        if not required_grad:
            self.model.requires_grad_(False)
    
        # define 
        self.extract= ExtraRoberta(method= 'mean')
        # only support transformer-based encoder output dim = 786
        self.attention_context= AttentionWithContext(units= hidden_dim, )

        # dropout
        self.drp1= nn.Dropout(p= dropout)
        self.drp2= nn.Dropout(p= dropout)
        
        # defind output 
        if not num_label: 
            self.fc= nn.Linear(hidden_dim * 2 + 1, 128)
        else:
            self.fc= nn.Linear(hidden_dim * 2 + 1, num_label)

        nn.init.xavier_uniform_(self.fc.weight)
        nn.init.zeros_(self.fc.bias)

    def get_embedding(self, inputs): 
        embedding_bert= self.model(**inputs)
        embedding_enhance= self.extract(embedding_bert.hidden_states)

        # x= self.lnrom(embedding_enhance)
        x= self.drp1(embedding_enhance)
        x= self.attention_context(x)

        return x 
    
    def forward(self, inputs_left, inputs_right): 
        x_left = self.get_embedding(inputs_left)
        x_right= self.get_embedding(inputs_right)

        x = torch.concat((x_left, x_right, torch.norm(x_right - x_left, p= 2, dim= -1).view(-1, 1)), dim= -1)
        x = self.drp2(x)
        x = self.fc(x)

        return x 

### Sentence Bert
### By default, while training sentence bert, we use cosine similarity loss 
class SentenceBert: 
    def __init__(self, model_name= 'vinai/phobert-base-v2', required_grad= True, num_label= 1,
                 dropout= 0.1, hidden_dim= 768, torch_dtype= torch.float16, device= None):
    
        self.model= BiEncoder(model_name, required_grad, dropout, hidden_dim, num_label)
        # self.model.to(device, dtype= torch_dtype)
        self.tokenizer= AutoTokenizer.from_pretrained(model_name, use_fast= True, add_prefix_space= True)
        self.device= device 
        self.torch_dtype= torch_dtype
    
    def load_ckpt(self, path): 
        self.model.load_state_dict(torch.load(path, map_location= 'cpu')['model_state_dict'])
        self.model.to(self.device, dtype= self.torch_dtype)

    def _preprocess(self): 
        if self.model.training: 
            self.model.eval() 
    
    def _preprocess_tokenize(self, text): 
        inputs= self.tokenizer.batch_encode_plus(text, return_tensors= 'pt', 
                            padding= 'longest', max_length= 256, truncation= True)
        
        return inputs
    
    def encode(self, text: List[str]): 
        self._preprocess()

        # batch_text= list(map(lambda x: TextFormat.preprocess_text(x), text))
        inputs= self._preprocess_tokenize(text)
        # print(inputs)

        with torch.no_grad(): 
            embedding= self.model.get_embedding(dict( (i, j.to(self.device)) for i,j in inputs.items()))
                
        return torch.tensor(embedding) 




    


