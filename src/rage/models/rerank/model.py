from typing import List
import numpy as np 
import torch 
import torch.nn as nn 
from transformers import AutoTokenizer

from rag_chatbot.trainer.argument import ArgumentDataset, ArgumentTrain
from ..model_rag import ModelRag
from ..model_infer import InferModel
from ...trainer.trainer import _TrainerCrossEncoder
from ..componets import ExtraRoberta, load_backbone, PoolingStrategy
from ...utils.process_bar import Progbar
from ...utils.convert_data import _convert_data 


### Cross-encoder
class Reranker(ModelRag, InferModel): 
    # using 
    def __init__(
        self, 
        model_name= 'vinai/phobert-base-v2', 
        type_backbone= 'mlm',
        aggregation_hidden_states= True, 
        required_grad_base_model= False, 
        strategy_pooling= "attention_context", 
        dropout= 0.1, 
        num_label= 1,
        torch_dtype= torch.float32, 
        device= None, 
        quantization_config= None,
        torch_compile= False, 
        backend_torch_compile: str= None
    ):
        
        super().__init__()
        
        self.aggregation_hidden_states= aggregation_hidden_states
        self.strategy_pooling= strategy_pooling
        self.type_backbone= type_backbone
        self.requires_grad_base_model= required_grad_base_model
        self.dropout= dropout
        self.torch_dtype= torch_dtype
        self.device= device
        self.quantization_config= quantization_config
        self.backend_torch_compile= backend_torch_compile

        self.tokenizer= AutoTokenizer.from_pretrained(model_name, add_prefix_space= True, use_fast= True)
        
        self.model= load_backbone(model_name, type_backbone= type_backbone, dropout= dropout,
                                using_hidden_states= aggregation_hidden_states)
        
        self.pooling= PoolingStrategy(strategy= strategy_pooling, units= self.model.config.hidden_size)

        if not required_grad_base_model:
            self.model.requires_grad_(False)
    
        # define 
        if self.aggregation_hidden_states:
            self.extract= ExtraRoberta(method= 'mean')
        
        if strategy_pooling in ["attention_context", "dense_avg", "dense_first", "dense_max"]: 
            self.drp1= nn.Dropout(p= dropout)

        # dropout 
        self.dropout_embedding= nn.Dropout(p= dropout)

        # defind output 
        self.fc= nn.Linear(self.model.config.hidden_size, num_label)
        nn.init.xavier_uniform_(self.fc.weight)
        nn.init.zeros_(self.fc.bias)


        self._set_dtype_device()

        if torch_compile:
            self._compile_with_torch()

    def get_embedding(self, inputs):
        embedding= self.model(**inputs)

        if self.aggregation_hidden_states: 
            embedding= self.extract(embedding.hidden_states)
        else:
            embedding= embedding.last_hidden_state

        if self.strategy_pooling in ["attention_context", "dense_avg", "dense_first", "dense_max"]: 
            embedding= self.drp1(embedding)
        x= self.pooling(embedding)

        return x
    
    def compile(
        self, 
        argument_train: type[ArgumentTrain], 
        argument_dataset: type[ArgumentDataset]
    ):
        self._trainer= _TrainerCrossEncoder(self, argument_train, argument_dataset)
    
    def _get_config_model_base(self):
        return {
            "model_type_base": self.__class__.__name__, 
            "model_name": self.model_name, 
            "type_backbone": self.type_backbone,
            "required_grad_base_model": self.requires_grad_base_model, 
            "aggregation_hidden_states": self.aggregation_hidden_states,
            "dropout": self.dropout,
            "quantization_config": self.quantization_config
        }

    def  _get_config_addition_weight(self):
        return {
            "strategy_pooling": self.strategy_pooling
        }
    
    def get_config(self):
        self.modules_cfg= {
            "model_base": self._get_config_model_base(), 
            "pooling": self._get_config_addition_weight()
        }
        return super().get_config()
    
    def forward(self, inputs): 
        x= self.get_embedding(inputs)
        x= self.dropout_embedding(x)
        x= self.fc(x)

        return x

    def _preprocess(self):
        if self.training: 
            self.eval()
    
    def _preprocess_tokenize(self, text, max_length): 
        inputs= self.tokenizer.batch_encode_plus(text, return_tensors= 'pt', 
                            padding= 'longest', max_length= max_length, truncation= True)
        return inputs
    
    def _execute_per_batch(
            self, 
            text: List[list[str]], 
            max_length= 256
        ): 
        
        batch_text= list(map(lambda x: self.tokenizer.sep_token.join([x[0], x[1]]), text))
        inputs= self._preprocess_tokenize(batch_text, max_length)

        with torch.no_grad(): 
            embedding= self(dict( (i, j.to(self.device)) for i,j in inputs.items()))
        
        return nn.Sigmoid()(torch.tensor(embedding))

    def rank(
        self, 
        text: List[list[str]], 
        batch_size= 64, 
        max_length= 256, 
        return_tensors= 'np', 
        verbose= 1
    ):  # [[a, b], [c, d]]
        results= [] 
        self._preprocess()

        if batch_size > len(text):
            batch_size= len(text)

        batch_text= np.array_split(text, len(text)// batch_size)
        pbi= Progbar(len(text), verbose= verbose, unit_name= "Samples")

        for batch in batch_text: 
            results.append(self._execute_per_batch(batch.tolist(), max_length))

            pbi.add(len(batch))

        return _convert_data(torch.concat(results).view(-1,).clone().detach(), return_tensors= return_tensors)